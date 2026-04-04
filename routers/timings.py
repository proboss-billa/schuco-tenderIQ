import uuid

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from core.database import get_db
from core.logging import _project_timings
from models.project import Project

router = APIRouter(prefix="", tags=["timings"])


@router.get("/projects/{project_id}/timings")
async def get_project_timings(project_id: uuid.UUID, db: Session = Depends(get_db)):
    """Return captured [TIMING] log entries for a project, split into summary and details."""
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    entries = _project_timings.get(str(project_id), [])
    summary = [e for e in entries if e["summary"]]
    details = [e for e in entries if not e["summary"]]

    # Derive total pipeline duration from the PIPELINE TOTAL entry if present
    total = next(
        (e["duration"] for e in reversed(summary)
         if e["tag"] == "PIPELINE" and "TOTAL" in e["detail"]),
        None,
    )

    return {
        "project_id":      str(project_id),
        "processing_status": project.processing_status,
        "total_seconds":   total,
        "summary":         summary,
        "details":         details,
        "all":             entries,
    }
