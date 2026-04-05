import asyncio
import json as _json
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from config.parameters import FACADE_PARAMETERS
from config.models import list_models, AVAILABLE_MODELS, DEFAULT_MODEL
from core.database import SessionLocal, get_db
from models.document import Document
from models.extracted_parameter import ExtractedParameter
from models.project import Project
from services import event_bus
from services.extraction import _run_extraction, _run_single_extraction

router = APIRouter(prefix="", tags=["parameters"])


@router.get("/projects/{project_id}/parameters")
async def get_extracted_parameters(project_id: uuid.UUID, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    parameters = db.query(ExtractedParameter).filter(
        ExtractedParameter.project_id == project_id
    ).all()

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

        # Back-fill all_sources from legacy fields if not present
        if not all_sources and (param.source_document or pages):
            doc_name = param.source_document.original_filename if param.source_document else None
            if doc_name or pages:
                all_sources = [{
                    "document_id": str(param.source_document_id) if param.source_document_id else None,
                    "document":    doc_name,
                    "pages":       pages,
                    "section":     param.source_section,
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


# ─── SSE streaming endpoint ─────────────────────────────────────────────────
#
# The streaming extraction coordinator publishes events via `services.event_bus`
# as documents index and parameters get (re)extracted. This endpoint exposes
# that stream over Server-Sent Events so the frontend can render live.
#
# Protocol (text/event-stream):
#   event: snapshot        — initial full state payload on connect
#   event: doc_indexed     — a document finished indexing
#   event: param_updated   — a parameter row was inserted or updated
#   event: pass_complete   — an incremental or final extraction pass finished
#   event: done            — coordinator loop has exited
#   event: error           — a fatal error in the coordinator
#
# Connection stays open until `done` is received or the client disconnects.
# A heartbeat comment is sent every 15s to keep proxies from timing out.

def _build_snapshot(project_id: uuid.UUID) -> dict:
    """Return the full current state for a project — same shape as `/parameters`.

    Runs in its own short-lived DB session so we don't leak connections.
    """
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.project_id == project_id).first()
        if not project:
            return {"error": "project not found"}

        params = db.query(ExtractedParameter).filter(
            ExtractedParameter.project_id == project_id
        ).all()

        param_payload = []
        for p in params:
            try:
                all_sources = _json.loads(p.all_sources) if p.all_sources else []
            except (ValueError, TypeError):
                all_sources = []
            param_payload.append({
                "parameter_key": p.parameter_name,
                "parameter_name": p.parameter_display_name,
                "value": p.value_text,
                "unit": p.unit,
                "confidence": float(p.confidence_score) if p.confidence_score is not None else None,
                "sources": all_sources,
                "lifecycle_status": getattr(p, "lifecycle_status", None),
                "change_count": getattr(p, "change_count", 0) or 0,
                "history": getattr(p, "history", None),
            })

        docs = db.query(Document).filter(Document.project_id == project_id).all()
        doc_payload = [
            {
                "document_id": str(d.document_id),
                "filename": d.original_filename,
                "file_type": d.file_type,
                "processing_status": getattr(d, "processing_status", "pending"),
            }
            for d in docs
        ]
        indexed_count = sum(
            1 for d in docs
            if getattr(d, "processing_status", "") in ("indexed", "completed")
        )

        return {
            "project_id": str(project_id),
            "processing_status": project.processing_status,
            "pipeline_step": getattr(project, "pipeline_step", None),
            "parameters": param_payload,
            "documents": doc_payload,
            "indexed_count": indexed_count,
            "total_count": len(docs),
        }
    finally:
        db.close()


def _sse_format(event_type: str, data: dict) -> str:
    """Encode a single SSE message (event + data lines + terminator)."""
    return f"event: {event_type}\ndata: {_json.dumps(data, default=str)}\n\n"


@router.get("/projects/{project_id}/parameters/stream")
async def stream_parameters(project_id: uuid.UUID):
    """Server-Sent Events stream of live extraction progress for a project."""
    # Validate project exists (short-lived session).
    _probe = SessionLocal()
    try:
        exists = _probe.query(Project).filter(Project.project_id == project_id).first()
        if not exists:
            raise HTTPException(status_code=404, detail="Project not found")
        project_status = exists.processing_status
    finally:
        _probe.close()

    async def event_gen():
        queue = event_bus.register_listener(project_id)
        try:
            # 1. Initial snapshot so the client has full state immediately.
            snapshot = _build_snapshot(project_id)
            yield _sse_format("snapshot", snapshot)

            # If the project is already finished, emit `done` and close — the
            # client doesn't need to wait for any live events.
            if project_status in ("completed", "failed"):
                yield _sse_format("done", {"reason": "already_" + project_status})
                return

            # 2. Stream live events until `done` or client disconnect.
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Heartbeat comment — keeps connection alive through proxies.
                    yield ": ping\n\n"
                    continue

                yield _sse_format(event["type"], event["payload"])
                if event["type"] == "done":
                    break
        except asyncio.CancelledError:
            # Client disconnected — nothing to do, cleanup happens in finally.
            pass
        finally:
            event_bus.unregister_listener(project_id, queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


@router.get("/health/event-bus")
async def event_bus_health():
    """Lightweight introspection of the SSE listener map.

    Useful for debugging leaked connections or diagnosing "why is nothing
    streaming" issues. Returns `{project_id: listener_count}` for every
    project that currently has at least one listener attached.
    """
    counts = event_bus.snapshot_listener_counts()
    return {
        "listener_counts": counts,
        "total_listeners": sum(counts.values()),
        "active_projects": len(counts),
    }


@router.get("/projects/{project_id}/parameters/{parameter_name}/sources")
async def get_parameter_sources(
    project_id: uuid.UUID,
    parameter_name: str,
    db: Session = Depends(get_db),
):
    """Return structured evidence (sources + history) for a single parameter.

    Frontends use this to power the "show evidence" modal and the value-
    change history popover without re-parsing `all_sources` JSON blobs
    client-side or joining tables themselves.
    """
    param = (
        db.query(ExtractedParameter)
        .filter(
            ExtractedParameter.project_id == project_id,
            ExtractedParameter.parameter_name == parameter_name,
        )
        .first()
    )
    if not param:
        raise HTTPException(status_code=404, detail="Parameter not found")

    # Parse the JSON-encoded columns defensively.
    try:
        all_sources = _json.loads(param.all_sources) if param.all_sources else []
    except (ValueError, TypeError):
        all_sources = []
    try:
        history = _json.loads(param.history) if param.history else []
    except (ValueError, TypeError):
        history = []

    # Join primary source chunk text for display (if any).
    chunk_text = None
    if param.source_chunk_id and param.source_chunk:
        chunk_text = param.source_chunk.chunk_text

    return {
        "parameter_key": param.parameter_name,
        "parameter_name": param.parameter_display_name,
        "value": param.value_text,
        "unit": param.unit,
        "confidence": float(param.confidence_score) if param.confidence_score is not None else None,
        "lifecycle_status": getattr(param, "lifecycle_status", None),
        "change_count": getattr(param, "change_count", 0) or 0,
        "last_changed_at": (
            param.last_changed_at.isoformat() + "Z"
            if getattr(param, "last_changed_at", None) else None
        ),
        "primary_source": {
            "document": param.source_document.original_filename if param.source_document else None,
            "document_id": str(param.source_document_id) if param.source_document_id else None,
            "page": param.source_page_number,
            "section": param.source_section,
            "subsection": param.source_subsection,
            "chunk_text": chunk_text,
        },
        "sources": all_sources,
        "history": history,
        "notes": param.notes,
    }


@router.post("/projects/{project_id}/re-extract", status_code=202)
async def re_extract_parameters(
    project_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    model: Optional[str] = Query(None, description="Model key: claude-opus-4, claude-sonnet-4, claude-haiku-3.5, gemini-flash"),
):
    """Re-run just the parameter extraction step on an already-processed project.
    Skips document parsing/embedding -- uses vectors already in Pinecone."""
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

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
    db: Session = Depends(get_db),
):
    """Re-run extraction for a single parameter. Uses vectors already in Pinecone."""
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

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
