import uuid

from fastapi import APIRouter, Form, HTTPException, Depends
from sqlalchemy.orm import Session

from core.database import get_db
from models.project import Project
from models.query_log import QueryLog
from services.query_service import process_query

router = APIRouter(prefix="", tags=["query"])


@router.post("/projects/{project_id}/query")
async def adhoc_query(project_id: uuid.UUID, query: str = Form(...), db: Session = Depends(get_db)):
    # Verify project exists
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return process_query(project_id, query, db)


@router.get("/projects/{project_id}/chat-history")
async def get_chat_history(project_id: uuid.UUID, db: Session = Depends(get_db)):
    """Return all chat messages for a project, ordered chronologically."""
    logs = (
        db.query(QueryLog)
        .filter(QueryLog.project_id == project_id)
        .order_by(QueryLog.created_at.asc())
        .all()
    )
    messages = []
    for log in logs:
        messages.append({
            "role": "user",
            "type": "text",
            "content": log.query_text,
            "timestamp": log.created_at.isoformat() if log.created_at else None,
        })
        if log.response_text:
            # Build source strings for display
            source_strs = []
            if log.sources_json:
                for s in log.sources_json:
                    label = s.get("document", "")
                    if s.get("page"):
                        label += f" \u00b7 Page {s['page']}"
                    if s.get("section"):
                        label += f" \u00b7 {s['section']}"
                    source_strs.append(label)
            messages.append({
                "role": "assistant",
                "type": "text",
                "content": log.response_text,
                "sources": source_strs,
                "timestamp": log.created_at.isoformat() if log.created_at else None,
            })
    return {"messages": messages}
