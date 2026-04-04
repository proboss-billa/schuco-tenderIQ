import uuid
from datetime import datetime
from pathlib import Path
from typing import List

import aiofiles
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from core.database import get_db
from models.document import Document
from models.project import Project
from services.file_classifier import classify_file_type
from services.pipeline import _run_pipeline

router = APIRouter(prefix="", tags=["projects"])


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
    for file in files:
        file_type = classify_file_type(file.filename)
        file_path = upload_dir / file.filename
        async with aiofiles.open(str(file_path), "wb") as buffer:
            while chunk := await file.read(1024 * 1024):  # 1 MB chunks
                await buffer.write(chunk)

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
        "project_type": project.project_type,
        "documents_uploaded": len(saved_documents),
        "status": "uploaded",
    }


@router.post("/projects/{project_id}/process", status_code=202)
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

    # Mark as processing immediately and return -- pipeline runs in background
    project.processing_status = "processing"
    project.processing_started_at = datetime.now()
    db.commit()

    background_tasks.add_task(_run_pipeline, project_id)

    return {
        "project_id": str(project_id),
        "status": "processing",
        "message": "Processing started. Poll /projects/{project_id} for status.",
    }
