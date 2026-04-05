import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import aiofiles
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from config.models import AVAILABLE_MODELS, DEFAULT_MODEL
from core.database import get_db
from models.document import Document
from models.document_chunk import DocumentChunk
from models.project import Project
from services.file_classifier import classify_content_type
from services.pipeline import _run_pipeline

logger = logging.getLogger("tenderiq.projects")
router = APIRouter(prefix="", tags=["projects"])

MAX_FILE_SIZE = 100 * 1024 * 1024    # 100 MB per file
MAX_TOTAL_SIZE = 500 * 1024 * 1024   # 500 MB total per project


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return [
        {
            "project_id": str(p.project_id),
            "project_name": p.project_name,
            "project_type": getattr(p, "project_type", "commercial") or "commercial",
            "processing_status": p.processing_status,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": (p.updated_at.isoformat() if getattr(p, "updated_at", None) else
                           p.created_at.isoformat() if p.created_at else None),
        }
        for p in projects
    ]


@router.post("/projects/create")
async def create_project(
    project_name: str = Form(...),
    project_description: str = Form(None),
    project_type: str = Form("commercial"),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    # Validate project_type
    p_type = project_type.lower().strip() if project_type else "commercial"
    if p_type not in ("commercial", "residential"):
        p_type = "commercial"

    project = Project(
        project_name=project_name,
        project_description=project_description,
        project_type=p_type,
        processing_status="uploaded",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    upload_dir = Path(f"uploads/{project.project_id}")
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_documents = []
    total_size = 0
    for file in files:
        file_path = upload_dir / file.filename
        file_size = 0
        async with aiofiles.open(str(file_path), "wb") as buffer:
            while chunk := await file.read(1024 * 1024):  # 1 MB chunks
                file_size += len(chunk)
                if file_size > MAX_FILE_SIZE:
                    file_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File '{file.filename}' exceeds 100 MB limit.",
                    )
                await buffer.write(chunk)

        total_size += file_size
        if total_size > MAX_TOTAL_SIZE:
            file_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=413,
                detail="Total upload size exceeds 500 MB limit.",
            )

        # Classify AFTER save so content sampling can catch CAD drawings
        # with generic filenames (e.g. "1.pdf", "scan.pdf"). Falls back to
        # filename-only classification if content sampling fails.
        file_type = classify_content_type(file.filename, str(file_path))

        document = Document(
            project_id=project.project_id,
            original_filename=file.filename,
            file_type=file_type,
            file_size_bytes=file_size,
            file_path=str(file_path),
        )
        db.add(document)
        saved_documents.append(document)

    db.commit()

    return {
        "project_id": str(project.project_id),
        "project_name": project.project_name,
        "project_type": project.project_type,
        "documents_uploaded": len(saved_documents),
        "status": "uploaded",
    }


@router.post("/projects/{project_id}/process", status_code=202)
async def process_project(
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    model: Optional[str] = Query(None, description="Model key for extraction"),
    ocr_engine: Optional[str] = Query(
        "auto",
        description="OCR engine for image pages: 'auto' (Mistral + Gemini fallback), "
                    "'mistral' (Mistral only, fastest), 'gemini' (Gemini only, thorough)",
    ),
):
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.processing_status == "completed":
        return {"message": "Already processed", "project_id": str(project_id)}

    if project.processing_status == "processing":
        return {"message": "Already processing", "project_id": str(project_id)}

    # Validate model key if provided
    model_key = model if model and model in AVAILABLE_MODELS else None

    # Validate OCR engine choice
    ocr_choice = ocr_engine if ocr_engine in ("auto", "mistral", "gemini") else "auto"

    # Mark as processing immediately and return -- pipeline runs in background
    project.processing_status = "processing"
    project.processing_started_at = datetime.now()
    project.error_message = None  # clear previous errors
    db.commit()

    background_tasks.add_task(
        _run_pipeline, project_id, model_key=model_key, ocr_engine=ocr_choice
    )

    return {
        "project_id": str(project_id),
        "status": "processing",
        "model": model_key or DEFAULT_MODEL,
        "ocr_engine": ocr_choice,
        "message": "Processing started. Poll /projects/{project_id} for status.",
    }


@router.delete("/projects/{project_id}")
def delete_project(project_id: uuid.UUID, db: Session = Depends(get_db)):
    """Permanently delete a project and ALL related data.

    Cleans up:
    1. Pinecone vectors (all chunks for this project)
    2. PostgreSQL records (CASCADE handles documents, chunks, params, BOQ, runs, logs)
    3. Uploaded files on disk
    """
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_name = project.project_name
    logger.info(f"[DELETE] Deleting project '{project_name}' ({project_id})")

    # ── Step 1: Delete Pinecone vectors ──────────────────────────────────────
    pinecone_ids = (
        db.query(DocumentChunk.pinecone_id)
        .filter(
            DocumentChunk.project_id == project_id,
            DocumentChunk.pinecone_id.isnot(None),
        )
        .all()
    )
    pinecone_ids = [pid[0] for pid in pinecone_ids if pid[0]]

    if pinecone_ids:
        try:
            from core.clients import pinecone_index
            BATCH = 100
            for i in range(0, len(pinecone_ids), BATCH):
                batch = pinecone_ids[i:i + BATCH]
                try:
                    pinecone_index.delete(ids=batch)
                except Exception as e:
                    logger.warning(f"[DELETE] Pinecone batch delete failed: {e}")
            logger.info(f"[DELETE] Removed {len(pinecone_ids)} vectors from Pinecone")
        except Exception as e:
            logger.warning(f"[DELETE] Pinecone cleanup failed (non-fatal): {e}")

    # ── Step 2: Delete project from PostgreSQL (CASCADE cleans related rows) ─
    db.delete(project)
    db.commit()
    logger.info(f"[DELETE] Removed project '{project_name}' from database")

    # ── Step 3: Delete uploaded files from disk ──────────────────────────────
    upload_dir = Path(f"uploads/{project_id}")
    if upload_dir.exists():
        try:
            shutil.rmtree(upload_dir)
            logger.info(f"[DELETE] Removed upload directory: {upload_dir}")
        except Exception as e:
            logger.warning(f"[DELETE] File cleanup failed (non-fatal): {e}")

    return {
        "message": f"Project '{project_name}' deleted successfully",
        "project_id": str(project_id),
        "vectors_removed": len(pinecone_ids),
    }


# ── MIME type mapping for document serving ───────────────────────────────────
_MIME_TYPES = {
    ".pdf":  "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls":  "application/vnd.ms-excel",
    ".csv":  "text/csv",
    ".ods":  "application/vnd.oasis.opendocument.spreadsheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc":  "application/msword",
    ".dxf":  "application/dxf",
    ".dwg":  "application/acad",
}


@router.get("/projects/{project_id}/documents/{document_id}/file")
def serve_document_file(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Serve an uploaded document file for preview.

    Returns the raw file with correct Content-Type so browsers can render
    PDFs inline, open spreadsheets, etc.  Used by the frontend to open
    source documents when a user clicks a parameter's source reference.
    """
    document = (
        db.query(Document)
        .filter(
            Document.document_id == document_id,
            Document.project_id == project_id,
        )
        .first()
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = Path(document.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    ext = file_path.suffix.lower()
    media_type = _MIME_TYPES.get(ext, "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=document.original_filename,
        # Allow browser to render inline (PDF viewer) instead of forcing download
        headers={"Content-Disposition": f'inline; filename="{document.original_filename}"'},
    )


@router.get("/projects/{project_id}/documents")
def list_project_documents(project_id: uuid.UUID, db: Session = Depends(get_db)):
    """List all documents in a project with their metadata."""
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    documents = (
        db.query(Document)
        .filter(Document.project_id == project_id)
        .order_by(Document.original_filename)
        .all()
    )
    return [
        {
            "document_id": str(d.document_id),
            "filename": d.original_filename,
            "file_type": d.file_type,
            "file_size_bytes": d.file_size_bytes,
            "page_count": getattr(d, "page_count", None),
            "num_chunks": getattr(d, "num_chunks", None),
            "processing_status": d.processing_status,
        }
        for d in documents
    ]
