import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import aiofiles
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks, Query
from sqlalchemy.orm import Session

from config.models import AVAILABLE_MODELS, DEFAULT_MODEL
from core.database import get_db
from models.document import Document
from models.project import Project
from services.file_classifier import classify_file_type
from services.pipeline import _run_pipeline

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
        file_type = classify_file_type(file.filename)
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

    # Mark as processing immediately and return -- pipeline runs in background
    project.processing_status = "processing"
    project.processing_started_at = datetime.now()
    project.error_message = None  # clear previous errors
    db.commit()

    background_tasks.add_task(_run_pipeline, project_id, model_key=model_key)

    return {
        "project_id": str(project_id),
        "status": "processing",
        "model": model_key or DEFAULT_MODEL,
        "message": "Processing started. Poll /projects/{project_id} for status.",
    }
