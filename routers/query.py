import uuid
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Depends
from sqlalchemy.orm import Session

from auth.utils import get_current_user
from config.models import AVAILABLE_MODELS
from core.database import get_db
from models.document import Document
from models.project import Project
from models.query_log import QueryLog
from models.user import User
from services.query_service import process_query
# from services.credit_service import check_credits, deduct_tokens  # custom token tracking (parked)

router = APIRouter(prefix="", tags=["query"])


@router.post("/projects/{project_id}/query")
async def adhoc_query(
    project_id: uuid.UUID,
    query: str = Form(...),
    model: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Verify project exists and belongs to current user
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.user_id is not None and project.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # check_credits(current_user)  # custom token tracking (parked)

    model_key = model if model and model in AVAILABLE_MODELS else None
    result = process_query(project_id, query, db, model_key=model_key)

    # if result.get("tokens_used", 0) > 0:  # custom token tracking (parked)
    #     deduct_tokens(db, current_user.user_id, result["tokens_used"])

    return result


@router.get("/projects/{project_id}/chat-history")
async def get_chat_history(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all chat messages for a project, ordered chronologically."""
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.user_id is not None and project.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    logs = (
        db.query(QueryLog)
        .filter(QueryLog.project_id == project_id)
        .order_by(QueryLog.created_at.asc())
        .all()
    )

    # Build filename → document_id lookup for backfilling old sources
    # that were stored without document_id.
    doc_rows = (
        db.query(Document.document_id, Document.original_filename)
        .filter(Document.project_id == project_id)
        .all()
    )
    fname_to_id = {r.original_filename: str(r.document_id) for r in doc_rows}

    def _enrich_sources(raw_sources):
        """Ensure every source dict has document_id (backfill from filename)."""
        if not raw_sources:
            return []
        enriched = []
        for s in raw_sources:
            if isinstance(s, str):
                # Legacy string — try to parse out filename for lookup
                enriched.append(s)
                continue
            if isinstance(s, dict) and not s.get("document_id"):
                doc_name = s.get("document", "")
                s["document_id"] = fname_to_id.get(doc_name)
            enriched.append(s)
        return enriched

    messages = []
    for log in logs:
        messages.append({
            "role": "user",
            "type": "text",
            "content": log.query_text,
            "timestamp": log.created_at.isoformat() if log.created_at else None,
        })
        if log.response_text:
            messages.append({
                "role": "assistant",
                "type": "text",
                "content": log.response_text,
                "sources": _enrich_sources(log.sources_json),
                "timestamp": log.created_at.isoformat() if log.created_at else None,
            })
    return {"messages": messages}
