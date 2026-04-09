import json as _json
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from sqlalchemy.orm import Session, joinedload

from auth.utils import get_current_user
from config.parameters import FACADE_PARAMETERS
from config.models import list_models, AVAILABLE_MODELS, DEFAULT_MODEL
from core.database import get_db
from models.document import Document
from models.extracted_parameter import ExtractedParameter
from models.project import Project
from models.user import User
from services.extraction import _run_extraction, _run_single_extraction

router = APIRouter(prefix="", tags=["parameters"])


@router.get("/projects/{project_id}/parameters")
async def get_extracted_parameters(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.user_id is not None and project.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    parameters = (
        db.query(ExtractedParameter)
        .options(
            joinedload(ExtractedParameter.source_document),
            joinedload(ExtractedParameter.source_chunk),
        )
        .filter(ExtractedParameter.project_id == project_id)
        .all()
    )

    results = []
    for param in parameters:
        # Parse legacy pages list
        try:
            pages = _json.loads(param.source_pages) if param.source_pages else []
        except (ValueError, TypeError):
            pages = []
        if not pages and param.source_page_number is not None:
            pages = [param.source_page_number]

        # Parse rich multi-document sources list
        try:
            all_sources = _json.loads(param.all_sources) if param.all_sources else []
        except (ValueError, TypeError):
            all_sources = []

        # Back-fill all_sources from legacy fields if not present.
        # Require BOTH doc_name AND pages — the old `or` branch surfaced the
        # chunk_dicts[0] fallback bug (pre-fix extractions had source_document_id
        # set to an arbitrary first-chunk doc even when the LLM didn't cite).
        if not all_sources and param.source_document:
            doc_name = param.source_document.original_filename if param.source_document else None
            if doc_name and pages:
                all_sources = [{
                    "document_id": str(param.source_document_id) if param.source_document_id else None,
                    "document":    doc_name,
                    "pages":       pages,
                    "sections":    [param.source_section] if param.source_section else [],
                }]

        # Fetch source chunk text for "show evidence" in UI
        chunk_text = None
        if param.source_chunk_id and param.source_chunk:
            chunk_text = param.source_chunk.chunk_text

        # Detect multi-document sourcing (potential conflict worth showing)
        unique_docs = {s.get("document_id") for s in all_sources if s.get("document_id")}
        multi_source = len(unique_docs) > 1

        results.append({
            "parameter_name": param.parameter_display_name,
            "parameter_key": param.parameter_name,
            "value": param.value_text,
            "unit": param.unit,
            "confidence": float(param.confidence_score) if param.confidence_score is not None else None,
            # Primary source (backwards compatible)
            "source": {
                "document": param.source_document.original_filename if param.source_document else None,
                "page": param.source_page_number,
                "pages": pages,
                "section": param.source_section,
                "subsection": param.source_subsection,
            },
            # Full multi-document source list (new)
            "sources": all_sources,
            "notes": param.notes,
            # Source evidence text -- shown in detail modal
            "source_text": chunk_text,
            # True when value was drawn from multiple documents (show multi-source badge)
            "multi_source": multi_source,
        })

    docs = db.query(Document).filter(Document.project_id == project_id).all()
    documents_info = [
        {
            "document_id": str(d.document_id),
            "filename": d.original_filename,
            "file_type": d.file_type,
            "processing_status": getattr(d, 'processing_status', 'completed'),
            "processing_error": getattr(d, 'processing_error', None),
            "page_count": getattr(d, 'page_count', None),
            "num_chunks": d.num_chunks,
            "is_archived": getattr(d, "is_archived", False),
            "archived_at": getattr(d, "archived_at", None).isoformat() if getattr(d, "archived_at", None) else None,
        }
        for d in docs
    ]

    return {
        "project_id": str(project_id),
        "project_type": getattr(project, "project_type", "commercial") or "commercial",
        "processing_status": project.processing_status,
        "pipeline_step": getattr(project, 'pipeline_step', None),
        "error_message": getattr(project, 'error_message', None),
        "parameters": results,
        "total_extracted": len(results),
        "documents": documents_info,
    }


@router.post("/projects/{project_id}/re-extract", status_code=202)
async def re_extract_parameters(
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    model: Optional[str] = Query(None, description="Model key: claude-opus-4, claude-sonnet-4, claude-haiku-3.5, gemini-flash"),
):
    """Re-run just the parameter extraction step on an already-processed project.
    Skips document parsing/embedding -- uses vectors already in Pinecone."""
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.user_id is not None and project.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Idempotency guard: prevent concurrent re-extractions
    if project.processing_status == "processing":
        raise HTTPException(
            status_code=409,
            detail="Extraction is already running. Please wait for it to complete.",
        )

    # Validate model key if provided
    model_key = model if model and model in AVAILABLE_MODELS else None

    background_tasks.add_task(_run_extraction, project_id, model_key=model_key)
    return {
        "project_id": str(project_id),
        "status": "re-extracting",
        "model": model_key or DEFAULT_MODEL,
        "message": "Extraction started. Check /projects/{project_id}/parameters after ~60s.",
    }


@router.post("/projects/{project_id}/parameters/{param_name}/re-extract", status_code=202)
async def re_extract_single_parameter(
    project_id: uuid.UUID,
    param_name: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-run extraction for a single parameter. Uses vectors already in Pinecone."""
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.user_id is not None and project.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Find the parameter config by name
    param_config = next((p for p in FACADE_PARAMETERS if p['name'] == param_name), None)
    if not param_config:
        raise HTTPException(status_code=404, detail=f"Unknown parameter: {param_name}")

    background_tasks.add_task(_run_single_extraction, project_id, param_config)
    return {
        "project_id": str(project_id),
        "parameter_name": param_name,
        "status": "re-extracting",
        "message": f"Re-extraction of '{param_config['display_name']}' started.",
    }


@router.get("/models")
def get_available_models():
    """List available LLM models for extraction."""
    return {
        "models": list_models(),
        "default": DEFAULT_MODEL,
    }
