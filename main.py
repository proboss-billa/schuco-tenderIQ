# main.py
import os
import traceback
from datetime import datetime

import anthropic
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import JSONResponse
from typing import List
import uuid
import shutil
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from pinecone import Pinecone

from auth.utils import create_access_token, verify_password, hash_password
from extraction.parameter_extractor import ParameterExtractor
from models.base import Base
from models.document import Document
from models.document_chunk import DocumentChunk
from models.extracted_parameter import ExtractedParameter
from models.project import Project
from models.query_log import QueryLog
from models.boq_item import BOQItem
from models.user import User
from processing.document_processor import DocumentProcessor
from voyage_embedding import VoyageEmbedding

from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.genai.errors import ClientError

app = FastAPI(title="Tender Analysis POC API", debug=True)

def initialize_pinecone():
    api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX", "tender-poc")

    pc = Pinecone(api_key=api_key)

    # Assumes index already exists (simplest for POC)
    index = pc.Index(index_name)

    return index

def get_db_session():
    DB_URL = os.getenv(
        "DATABASE_URL",
        "postgresql://poc_user:poc_password@postgres:5432/tender_poc"
    )

    engine = create_engine(DB_URL)
    SessionLocal = sessionmaker(bind=engine)

    return SessionLocal()

# Initialize services
db = get_db_session()
pinecone_index = initialize_pinecone()
embedding_client = VoyageEmbedding()
llm_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)
@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)

from pathlib import Path

def classify_file_type(filename: str) -> str:
    """
    Classifies uploaded file based on extension.
    Returns one of:
      - pdf_spec
      - docx_spec
      - excel_boq
    """
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return "pdf_spec"
    elif ext in [".docx", ".doc"]:
        return "docx_spec"
    elif ext in [".xlsx", ".xls"]:
        return "excel_boq"
    else:
        raise ValueError(f"Unsupported file type: {filename}")

@app.post("/projects/create")
async def create_project(
        project_name: str = Form(...),
        project_description: str = Form(None),
        files: List[UploadFile] = File(...),
):
    """
    Create new project and upload documents

    Returns: project_id
    """

    # Create project
    project = Project(
        project_name=project_name,
        project_description=project_description,
        processing_status='uploaded'
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    # Create upload directory
    upload_dir = Path(f"uploads/{project.project_id}")
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Save files
    saved_documents = []
    for file in files:
        # Determine file type
        file_type = classify_file_type(file.filename)

        # Save file
        file_path = upload_dir / file.filename
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Create document record
        document = Document(
            project_id=project.project_id,
            original_filename=file.filename,
            file_type=file_type,
            file_size_bytes=file_path.stat().st_size,
            file_path=str(file_path)
        )
        db.add(document)
        saved_documents.append(document)

    db.commit()

    return {
        "project_id": str(project.project_id),
        "project_name": project.project_name,
        "documents_uploaded": len(saved_documents),
        "status": "uploaded"
    }


@app.post("/projects/{project_id}/process")
async def process_project(project_id: uuid.UUID):
    """
    Process all documents for a project (SYNCHRONOUS)

    This will block until processing completes (5-10 minutes)
    """

    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.processing_status == 'completed':
        return {"message": "Already processed", "project_id": str(project_id)}

    # Update status
    project.processing_status = 'processing'
    project.processing_started_at = datetime.now()
    db.commit()

    try:
        # Process documents
        processor = DocumentProcessor(
            project_id=project_id,
            db_session=db,
            pinecone_index=pinecone_index,
            embedding_client=embedding_client,
            llm_client=llm_client
        )
        processor.process_all_documents()

        # Extract parameters
        extractor = ParameterExtractor(
            pinecone_index=pinecone_index,
            embedding_client=embedding_client,
            llm_client=llm_client,
            db_session=db
        )
        extractions = extractor.extract_all_parameters(str(project_id))

        # Update status
        project.processing_status = 'completed'
        project.processing_completed_at = datetime.now()
        db.commit()

        return {
            "project_id": str(project_id),
            "status": "completed",
            "parameters_extracted": len([e for e in extractions if e['found']]),
            "processing_time_seconds": (
                    project.processing_completed_at - project.processing_started_at
            ).total_seconds()
        }

    except Exception as e:
        traceback.print_exc()
        project.processing_status = 'failed'
        project.error_message = str(e)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


@app.get("/projects/{project_id}/parameters")
async def get_extracted_parameters(project_id: uuid.UUID):
    """
    Get all extracted parameters for a project

    Returns structured summary of all 25 parameters
    """

    parameters = db.query(ExtractedParameter).filter(
        ExtractedParameter.project_id == project_id
    ).all()

    results = []
    for param in parameters:
        results.append({
            "parameter_name": param.parameter_display_name,
            "value": param.value_text,
            "unit": param.unit,
            "confidence": float(param.confidence_score),
            "source": {
                "document": param.source_document.original_filename if param.source_document else None,
                "page": param.source_page_number,
                "section": param.source_section,
                "subsection": param.source_subsection
            },
            "notes": param.notes
        })

    return {
        "project_id": str(project_id),
        "parameters": results,
        "total_extracted": len(results)
    }


@app.post("/projects/{project_id}/query")
async def adhoc_query(project_id: uuid.UUID, query: str = Form(...)):
    """
    Ask ad-hoc question about the project

    Uses RAG to answer
    """

    # Search for relevant chunks
    query_embedding = embedding_client.embed([query])[0]

    results = pinecone_index.query(
        vector=query_embedding,
        top_k=5,
        filter={"project_id": str(project_id)},
        include_metadata=True
    )

    # Fetch chunks
    chunk_ids = [match['id'] for match in results['matches']]
    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.pinecone_id.in_(chunk_ids)
    ).all()

    # Build context
    context = "\n\n".join([
        f"[Source: {chunk.document.original_filename}, Page {chunk.page_number}, "
        f"Section: {chunk.section_title or 'N/A'}]\n{chunk.chunk_text}"
        for chunk in chunks[:3]
    ])

#     # LLM answer
#     prompt = f"""Answer the following question based on the provided context from tender documents.
#
# **Question:** {query}
#
# **Context:**
# {context}
#
# **Instructions:**
# - Provide a clear, direct answer
# - Include specific values and units where applicable
# - Cite the source (document, page, section) for your answer
# - If the answer is not in the context, say "Information not found in documents"
#
# **Answer:**"""
#
#     response = llm_client.messages.create(
#         model="claude-3-5-sonnet-20241022",
#         max_tokens=500,
#         messages=[{"role": "user", "content": prompt}]
#     )
#
#     answer = response.content[0].text

    # Define the behavior in the System Instruction
    system_prompt = """You are an expert tender analyst. Answer questions based ONLY on the provided context.
    - Provide clear, direct answers with specific values/units.
    - Cite the source (document, page, section).
    - If the answer isn't present, say "Information not found in documents"."""

    # The User Prompt now only contains the data
    user_content = f"Question: {query}\n\nContext:\n{context}"

    # Execute call
    response = gemini_client.models.generate_content(
        model="gemini-3-flash-preview",  # Current standard as of March 2026
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=500,
            temperature=0.1  # Low temperature is better for factual RAG
        )
    )

    answer = response.text

    # Log query
    query_log = QueryLog(
        project_id=project_id,
        query_text=query,
        query_type='adhoc',
        response_text=answer,
        num_sources_used=len(chunks)
    )
    db.add(query_log)
    db.commit()

    return {
        "query": query,
        "answer": answer,
        "sources": [
            {
                "document": chunk.document.original_filename,
                "page": chunk.page_number,
                "section": chunk.section_title,
                "subsection": chunk.subsection_title
            }
            for chunk in chunks[:3]
        ]
    }

@app.post("/signup")
def signup(email: str, password: str, db: Session = Depends(get_db_session)):
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=email,
        password_hash=hash_password(password)
    )

    db.add(user)
    db.commit()

    return {"message": "User created"}

@app.post("/login")
def login(email: str, password: str, db: Session = Depends(get_db_session)):
    user = db.query(User).filter(User.email == email).first()

    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": str(user.user_id)})

    return {"access_token": token, "token_type": "bearer"}