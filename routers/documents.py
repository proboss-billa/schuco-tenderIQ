import uuid

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from core.database import get_db
from models.document import Document
from models.project import Project
from services.extraction import _run_doc_reprocess

router = APIRouter(prefix="", tags=["documents"])


@router.post("/projects/{project_id}/documents/{document_id}/reprocess", status_code=202)
async def reprocess_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Re-process a single document: re-parse, re-embed, re-index, then re-extract parameters."""
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    doc = db.query(Document).filter(
        Document.document_id == document_id,
        Document.project_id == project_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    background_tasks.add_task(_run_doc_reprocess, project_id, document_id)
    return {
        "project_id": str(project_id),
        "document_id": str(document_id),
        "status": "reprocessing",
        "message": f"Re-processing '{doc.original_filename}' started.",
    }
