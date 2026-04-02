# main.py
import os
import traceback
from dotenv import load_dotenv

from config.parameters import FACADE_PARAMETERS

load_dotenv()
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import uuid
import shutil
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from pinecone import Pinecone

from auth.utils import create_access_token, verify_password, hash_password, decode_token, security
from extraction.parameter_extractor import ParameterExtractor
from models.base import Base
from models.document import Document
from models.document_chunk import DocumentChunk
from models.extracted_parameter import ExtractedParameter
from models.project import Project
from models.query_log import QueryLog
from models.user import User
from processing.document_processor import DocumentProcessor
from google_embedding import GoogleEmbedding

from google import genai
from google.genai import types

app = FastAPI(title="Tender Analysis POC API", debug=True)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://poc_user:poc_password@localhost:5432/tender_poc")
# Railway provides postgres:// but SQLAlchemy requires postgresql://
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
def seed_default_user():
    """Create default user abc@sooru.ai on startup if it doesn't exist."""
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == "abc@sooru.ai").first()
        if not existing:
            user = User(user_id=uuid.uuid4(), email="abc@sooru.ai", password_hash=hash_password("12345678"))
            db.add(user)
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    # Add columns introduced after initial schema creation
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE extracted_parameters ADD COLUMN IF NOT EXISTS source_pages TEXT"
        ))
        conn.commit()

# ── Pinecone ──────────────────────────────────────────────────────────────────
def initialize_pinecone():
    api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX", "tender-poc")
    target_dim = 1536

    pc = Pinecone(api_key=api_key)
    existing = [i.name for i in pc.list_indexes()]

    if index_name in existing:
        info = pc.describe_index(index_name)
        if info.dimension != target_dim:
            pc.delete_index(index_name)
            existing = []

    if index_name not in existing:
        from pinecone import ServerlessSpec
        pc.create_index(
            name=index_name,
            dimension=target_dim,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )

    return pc.Index(index_name)

# ── Shared service clients (stateless, safe to share) ────────────────────────
pinecone_index = initialize_pinecone()
embedding_client = GoogleEmbedding()
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# ── Helpers ───────────────────────────────────────────────────────────────────
def classify_file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return "pdf_spec"
    elif ext in [".docx", ".doc"]:
        return "docx_spec"
    elif ext in [".xlsx", ".xls"]:
        return "excel_boq"
    else:
        raise ValueError(f"Unsupported file type: {filename}")

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return [
        {
            "project_id": str(p.project_id),
            "project_name": p.project_name,
            "processing_status": p.processing_status,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in projects
    ]


@app.post("/projects/create")
async def create_project(
    project_name: str = Form(...),
    project_description: str = Form(None),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    project = Project(
        project_name=project_name,
        project_description=project_description,
        processing_status="uploaded",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    upload_dir = Path(f"uploads/{project.project_id}")
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_documents = []
    for file in files:
        file_type = classify_file_type(file.filename)
        file_path = upload_dir / file.filename
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        document = Document(
            project_id=project.project_id,
            original_filename=file.filename,
            file_type=file_type,
            file_size_bytes=file_path.stat().st_size,
            file_path=str(file_path),
        )
        db.add(document)
        saved_documents.append(document)

    db.commit()

    return {
        "project_id": str(project.project_id),
        "project_name": project.project_name,
        "documents_uploaded": len(saved_documents),
        "status": "uploaded",
    }


import logging as _logging
_pipeline_log = _logging.getLogger("pipeline")

async def _run_pipeline(project_id: uuid.UUID):
    """Background task: parse → embed → store → extract parameters."""
    _pipeline_log.info(f"[PIPELINE] Starting for project {project_id}")
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.project_id == project_id).first()
        if not project:
            _pipeline_log.error(f"[PIPELINE] Project {project_id} not found")
            return

        # ── Step 1: Document processing ──────────────────────────────────────
        _pipeline_log.info(f"[PIPELINE] Step 1: Document processing")
        processor = DocumentProcessor(
            project_id=project_id,
            db_session=db,
            pinecone_index=pinecone_index,
            embedding_client=embedding_client,
        )
        processor.process_all_documents()

        # Verify chunks were actually stored
        from models.document_chunk import DocumentChunk
        chunk_count = db.query(DocumentChunk).filter(
            DocumentChunk.project_id == project_id
        ).count()
        _pipeline_log.info(f"[PIPELINE] Step 1 done — {chunk_count} chunks in DB")

        db.expire_all()

        # ── Wait for Pinecone to index the newly upserted vectors ─────────────
        # Pinecone has eventual-consistency: vectors are not immediately queryable
        # after upsert. Poll with a dummy query until at least one vector for this
        # project is returned (or until a hard timeout of 30 s).
        _pipeline_log.info(f"[PIPELINE] Waiting for Pinecone to index vectors for project {project_id}…")
        import asyncio as _asyncio
        _dummy_vec = [0.0] * 1536
        deadline = 30  # max seconds to wait
        poll_interval = 2
        elapsed = 0
        while elapsed < deadline:
            await _asyncio.sleep(poll_interval)
            elapsed += poll_interval
            try:
                probe = pinecone_index.query(
                    vector=_dummy_vec,
                    top_k=1,
                    filter={"project_id": str(project_id)},
                    include_metadata=False,
                )
                hit_count = len(probe.get("matches", []))
            except Exception:
                hit_count = 0
            _pipeline_log.info(
                f"[PIPELINE] Pinecone probe after {elapsed}s: {hit_count} hit(s) for project"
            )
            if hit_count > 0:
                _pipeline_log.info(f"[PIPELINE] Pinecone ready — proceeding to extraction")
                break
        else:
            _pipeline_log.warning(f"[PIPELINE] Pinecone probe timed out after {deadline}s — proceeding anyway")

        # ── Step 2: Parameter extraction ─────────────────────────────────────
        _pipeline_log.info(f"[PIPELINE] Step 2: Parameter extraction")
        extractor = ParameterExtractor(
            pinecone_index=pinecone_index,
            embedding_client=embedding_client,
            db_session=db,
            session_factory=SessionLocal,
        )
        extractions = await extractor.extract_all_parameters_async(
            str(project_id), facade_parameters=FACADE_PARAMETERS
        )

        found_count = len([e for e in extractions if e.get("found")])
        _pipeline_log.info(f"[PIPELINE] Step 2 done — {found_count}/{len(extractions)} parameters found")

        # ── Step 3: Mark complete ─────────────────────────────────────────────
        project.processing_status = "completed"
        project.processing_completed_at = datetime.now()
        db.commit()
        _pipeline_log.info(f"[PIPELINE] Completed for project {project_id}")

    except Exception as e:
        traceback.print_exc()
        _pipeline_log.error(f"[PIPELINE] FAILED for project {project_id}: {e}")
        try:
            project = db.query(Project).filter(Project.project_id == project_id).first()
            if project:
                project.processing_status = "failed"
                project.error_message = str(e)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@app.post("/projects/{project_id}/process", status_code=202)
async def process_project(
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.processing_status == "completed":
        return {"message": "Already processed", "project_id": str(project_id)}

    if project.processing_status == "processing":
        return {"message": "Already processing", "project_id": str(project_id)}

    # Mark as processing immediately and return — pipeline runs in background
    project.processing_status = "processing"
    project.processing_started_at = datetime.now()
    db.commit()

    background_tasks.add_task(_run_pipeline, project_id)

    return {
        "project_id": str(project_id),
        "status": "processing",
        "message": "Processing started. Poll /projects/{project_id} for status.",
    }


@app.post("/projects/{project_id}/re-extract", status_code=202)
async def re_extract_parameters(
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Re-run just the parameter extraction step on an already-processed project.
    Skips document parsing/embedding — uses vectors already in Pinecone."""
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    async def _run_extraction(pid: uuid.UUID):
        _pipeline_log.info(f"[RE-EXTRACT] Starting for project {pid}")
        _db = SessionLocal()
        try:
            extractor = ParameterExtractor(
                pinecone_index=pinecone_index,
                embedding_client=embedding_client,
                db_session=_db,
                session_factory=SessionLocal,
            )
            extractions = await extractor.extract_all_parameters_async(
                str(pid), facade_parameters=FACADE_PARAMETERS
            )
            found_count = len([e for e in extractions if e.get("found")])
            _pipeline_log.info(f"[RE-EXTRACT] Done — {found_count}/{len(extractions)} parameters found")
        except Exception as e:
            import traceback as _tb
            _tb.print_exc()
            _pipeline_log.error(f"[RE-EXTRACT] FAILED for project {pid}: {e}")
        finally:
            _db.close()

    background_tasks.add_task(_run_extraction, project_id)
    return {
        "project_id": str(project_id),
        "status": "re-extracting",
        "message": "Extraction started. Check /projects/{project_id}/parameters after ~60s.",
    }


@app.get("/projects/{project_id}/parameters")
async def get_extracted_parameters(project_id: uuid.UUID, db: Session = Depends(get_db)):
    parameters = db.query(ExtractedParameter).filter(
        ExtractedParameter.project_id == project_id
    ).all()

    import json as _json
    results = []
    for param in parameters:
        # Parse stored pages JSON; fall back to primary page if missing
        try:
            pages = _json.loads(param.source_pages) if param.source_pages else []
        except (ValueError, TypeError):
            pages = []
        if not pages and param.source_page_number is not None:
            pages = [param.source_page_number]

        results.append({
            "parameter_name": param.parameter_display_name,
            "value": param.value_text,
            "unit": param.unit,
            "confidence": float(param.confidence_score),
            "source": {
                "document": param.source_document.original_filename if param.source_document else None,
                "page": param.source_page_number,
                "pages": pages,
                "section": param.source_section,
                "subsection": param.source_subsection,
            },
            "notes": param.notes,
        })

    return {
        "project_id": str(project_id),
        "parameters": results,
        "total_extracted": len(results),
    }


@app.post("/projects/{project_id}/query")
async def adhoc_query(project_id: uuid.UUID, query: str = Form(...), db: Session = Depends(get_db)):
    query_embedding = embedding_client.embed([query])[0]

    results = pinecone_index.query(
        vector=query_embedding,
        top_k=5,
        filter={"project_id": str(project_id)},
        include_metadata=True,
    )

    chunk_ids = [match["id"] for match in results["matches"]]
    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.pinecone_id.in_(chunk_ids)
    ).all()

    context = "\n\n".join([
        f"[Source {i+1}: {chunk.document.original_filename}, Page {chunk.page_number or 'N/A'}, "
        f"Section: {chunk.section_title or 'N/A'}]\n{chunk.chunk_text}"
        for i, chunk in enumerate(chunks[:3])
    ])

    system_prompt = """You are an expert tender analyst. Answer questions based ONLY on the provided context.
- Provide clear, direct answers with specific values and units.
- Always cite the page number (e.g. "Page 12") and document name where the information was found.
- If the answer isn't present, say "Information not found in documents"."""

    response = gemini_client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=f"Question: {query}\n\nContext:\n{context}",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=500,
            temperature=0.1,
        ),
    )

    answer = response.text

    query_log = QueryLog(
        project_id=project_id,
        query_text=query,
        query_type="adhoc",
        response_text=answer,
        num_sources_used=len(chunks),
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
                "subsection": chunk.subsection_title,
            }
            for chunk in chunks[:3]
        ],
    }


@app.get("/me")
def get_me(credentials=Depends(security), db: Session = Depends(get_db)):
    user_id = decode_token(credentials.credentials)
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": str(user.user_id), "email": user.email}


@app.post("/signup")
def signup(email: str, password: str, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=email, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    return {"message": "User created"}


@app.post("/login")
def login(email: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": str(user.user_id)})
    return {"access_token": token, "token_type": "bearer"}
