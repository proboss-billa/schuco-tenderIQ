# main.py
import asyncio
import contextvars
import logging as _logging_mod
import os
import re as _re
import time as _time_mod
import traceback
import aiofiles
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
from sqlalchemy.orm import sessionmaker, Session, joinedload

from pinecone import Pinecone

from auth.utils import create_access_token, verify_password, hash_password, decode_token, security
from extraction.parameter_extractor import ParameterExtractor, _get_llm_semaphore
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

# ── Per-project timing store ──────────────────────────────────────────────────
# Keyed by project_id string → list of timing dicts.
# Populated by _TimingHandler below; read by the /timings endpoint.
_project_timings: dict[str, list] = {}

# ContextVar that _TimingHandler reads to know which project is active.
# Set at the top of _run_pipeline and _run_extraction.
_timing_project_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_timing_project_id", default=None
)

_TIMING_RE = _re.compile(
    r"\[TIMING\]\[(?P<tag>[^\]]+)\](?:\[(?P<sub>[^\]]+)\])?\s+(?P<rest>.+?):\s+(?P<dur>\d+\.\d+)s"
)

# Human-readable labels for each timing tag
_TAG_LABELS = {
    "PARSE":         "Document parsed",
    "SECTION_GROUP": "Sections grouped",
    "BUILD_CHUNKS":  "Chunks built",
    "EMBED":         "Embeddings generated",
    "STORE":         "Chunks stored to DB + Pinecone",
    "BATCH":         "Batch complete",
    "DOC_TOTAL":     "Document indexed (end-to-end)",
    "BOQ_PARSE":     "BOQ parsed",
    "BOQ_TEXT_BUILD":"BOQ text prepared",
    "BOQ_PINECONE":  "BOQ uploaded to Pinecone",
    "BOQ_TOTAL":     "BOQ document indexed (end-to-end)",
    "EXTRACT_ALL":   "Full extraction round",
    "EXTRACT":       "Parameter extracted",
    "PIPELINE":      "Pipeline step",
}

# Tags to surface as "summary" (high-level) vs detailed
_SUMMARY_TAGS = {"DOC_TOTAL", "BOQ_TOTAL", "EXTRACT_ALL", "PIPELINE"}


class _TimingHandler(_logging_mod.Handler):
    """Capture [TIMING] log records and store them per active project."""

    def emit(self, record: _logging_mod.LogRecord):
        project_id = _timing_project_id.get()
        if not project_id:
            return
        msg = record.getMessage()
        if "[TIMING]" not in msg:
            return
        m = _TIMING_RE.search(msg)
        if not m:
            return
        tag  = m.group("tag")
        sub  = m.group("sub")    # e.g. parameter name for EXTRACT
        rest = m.group("rest").strip()
        dur  = float(m.group("dur"))
        label = _TAG_LABELS.get(tag, tag)
        if sub:
            label = f"{label}: {sub}"
        entry = {
            "tag":      tag,
            "sub":      sub,
            "label":    label,
            "detail":   rest,
            "duration": round(dur, 2),
            "ts":       _time_mod.time(),
            "summary":  tag in _SUMMARY_TAGS,
        }
        _project_timings.setdefault(project_id, []).append(entry)


_timing_handler = _TimingHandler()
_timing_handler.setLevel(_logging_mod.DEBUG)

# Attach to every logger that emits [TIMING] records
for _logger_name in (
    "processing.document_processor",
    "extraction.parameter_extractor",
    "pipeline",
):
    _logging_mod.getLogger(_logger_name).addHandler(_timing_handler)

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://poc_user:poc_password@localhost:5432/tender_poc")
# Railway provides postgres:// but SQLAlchemy requires postgresql://
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
)
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
        # ── Hierarchical chunking columns (parent-child chunk strategy) ───────
        # chunk_level: 0=section parent (not in Pinecone), 1=child (in Pinecone)
        conn.execute(text(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS "
            "chunk_level INTEGER NOT NULL DEFAULT 1"
        ))
        # parent_chunk_id: level-1 children reference their level-0 section parent
        conn.execute(text(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS "
            "parent_chunk_id UUID REFERENCES document_chunks(chunk_id) ON DELETE SET NULL"
        ))
        # prev/next links for in-section traversal
        conn.execute(text(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS prev_chunk_id UUID"
        ))
        conn.execute(text(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS next_chunk_id UUID"
        ))
        # parent chunks have no Pinecone ID — make the column nullable
        conn.execute(text(
            "ALTER TABLE document_chunks ALTER COLUMN pinecone_id DROP NOT NULL"
        ))
        conn.commit()
        # ── New columns for multi-file / large-file support ───────────────────
        conn.execute(text(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS processing_status VARCHAR(20) DEFAULT 'pending'"
        ))
        conn.execute(text(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS processing_error TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_count INTEGER"
        ))
        conn.execute(text(
            "ALTER TABLE extracted_parameters ADD COLUMN IF NOT EXISTS all_sources TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS pipeline_step TEXT"
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

# ── Global pipeline concurrency cap ──────────────────────────────────────────
# Limits simultaneous _run_pipeline executions to prevent DB pool exhaustion
# and Gemini rate-limit saturation when many projects are uploaded at once.
# Projects beyond the limit queue in the asyncio event loop rather than crashing.
# 3 concurrent pipelines × ~7 DB sessions each = 21 connections (within pool of 30).
_PIPELINE_SEMAPHORE = asyncio.Semaphore(3)

# ── Helpers ───────────────────────────────────────────────────────────────────
_DRAWING_FILENAME_KEYWORDS = [
    'drawing', 'drawings', 'tender drg', 'drg', 'elevation', 'elevations',
    'facade detail', 'curtain wall detail', 'cw detail', 'layout',
    'floor plan', 'site plan', 'detail sheet', 'detail drg',
]

def classify_file_type(filename: str) -> str:
    ext  = Path(filename).suffix.lower()
    stem = Path(filename).stem.lower()
    if ext == ".pdf":
        # Detect drawing PDFs by filename keywords before falling back to pdf_spec.
        # Content-based auto-detection (char-count sampling) still runs at parse
        # time inside _choose_pdf_parser and will catch drawing PDFs not matched
        # here. Together the two heuristics cover virtually all real tender sets.
        if any(kw in stem for kw in _DRAWING_FILENAME_KEYWORDS):
            return "pdf_drawing"
        return "pdf_spec"
    elif ext in [".docx", ".doc"]:
        return "docx_spec"
    elif ext in [".xlsx", ".xls"]:
        return "excel_boq"
    elif ext == ".dxf":
        return "dxf_drawing"
    elif ext == ".dwg":
        return "dwg_drawing"
    else:
        raise ValueError(f"Unsupported file type: {filename}. Supported: PDF, DOCX, XLSX, DXF, DWG")

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
        "documents_uploaded": len(saved_documents),
        "status": "uploaded",
    }


import logging as _logging
_pipeline_log = _logging.getLogger("pipeline")


async def _wait_for_pinecone_doc(project_id: str, document_id: str, timeout: int = 90) -> None:
    """Poll Pinecone until a specific document's vectors are queryable.

    Uses a per-document filter so we only proceed once THIS doc's chunks are
    visible — not just any old vector from a previous run.
    """
    dummy_vec = [0.0] * 1536
    elapsed = 0
    poll_interval = 3
    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        try:
            probe = pinecone_index.query(
                vector=dummy_vec,
                top_k=1,
                filter={"project_id": project_id, "document_id": document_id},
                include_metadata=False,
            )
            if probe.get("matches"):
                _pipeline_log.info(
                    f"[PIPELINE] Pinecone ready for doc {document_id} after {elapsed}s"
                )
                return
        except Exception as exc:
            _pipeline_log.warning(f"[PIPELINE] Pinecone probe error: {exc}")
    _pipeline_log.warning(
        f"[PIPELINE] Pinecone timed out for doc {document_id} after {timeout}s — proceeding anyway"
    )


async def _run_pipeline(project_id: uuid.UUID):
    """Queuing wrapper — acquires global pipeline slot, then delegates to inner."""
    import time as _time
    _timing_project_id.set(str(project_id))
    _pipeline_log.info(f"[PIPELINE] Queued project {project_id} — waiting for pipeline slot…")
    async with _PIPELINE_SEMAPHORE:
        await _run_pipeline_inner(project_id)


async def _run_pipeline_inner(project_id: uuid.UUID):
    """Background task: per-document parse → embed → extract parameters.

    Architecture
    ────────────
    Phase 1: Parse + embed ALL spec documents sequentially (shared DB session).
             After each parse, fire a concurrent asyncio.Task to watch for that
             document's vectors in Pinecone. All watch-tasks run in parallel so
             total Phase-1 wall time = parse_time_sum + max(single_pinecone_wait).
    Phase 2: Extraction runs ONCE after all docs are indexed — one set of
             18 batch LLM calls regardless of how many documents were uploaded.
    """
    import time as _time
    _timing_project_id.set(str(project_id))
    _project_timings[str(project_id)] = []   # reset on each new run
    t_pipeline_start = _time.perf_counter()
    _pipeline_log.info(f"[PIPELINE] Starting for project {project_id}")
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.project_id == project_id).first()
        if not project:
            _pipeline_log.error(f"[PIPELINE] Project {project_id} not found")
            return

        all_docs = db.query(Document).filter(
            Document.project_id == project_id,
            Document.processed == False,
        ).all()

        spec_docs = [d for d in all_docs if d.file_type in ['pdf_spec', 'docx_spec', 'dxf_drawing', 'dwg_drawing']]
        boq_docs  = [d for d in all_docs if d.file_type == 'excel_boq']
        total_spec = len(spec_docs)
        _pipeline_log.info(
            f"[PIPELINE] {len(spec_docs)} spec docs, {len(boq_docs)} BOQ docs"
        )

        processor = DocumentProcessor(
            project_id=project_id,
            db_session=db,
            pinecone_index=pinecone_index,
            embedding_client=embedding_client,
        )

        # ── Process BOQ documents (no LLM extraction needed) ─────────────────
        for doc in boq_docs:
            project.pipeline_step = f"Processing BOQ: {doc.original_filename}"
            doc.processing_status = "processing"
            db.commit()
            t_boq = _time.perf_counter()
            try:
                processor._process_boq_document(doc)
                doc.processing_status = "completed"
                _pipeline_log.info(
                    f"[TIMING][PIPELINE] BOQ {doc.original_filename}: "
                    f"{_time.perf_counter() - t_boq:.2f}s"
                )
            except Exception as e:
                _pipeline_log.error(f"[PIPELINE] BOQ failed {doc.original_filename}: {e}")
                doc.processing_status = "failed"
                doc.processing_error = str(e)[:1000]
            db.commit()

        # ── Phase 1: Parse all spec docs; Pinecone waits run concurrently ───────
        # Parse steps remain sequential — they share the 'db' session (not thread-safe).
        # After each parse succeeds, a concurrent asyncio.Task watches Pinecone for
        # that document's vectors. All watch-tasks overlap with subsequent parses.
        # Total Phase-1 wall time:
        #   (sum of parse times) + (longest single Pinecone wait)
        # vs old sequential: sum of (parse_time + pinecone_wait) per doc.
        # At 20 docs × 15s parse + 90s wait: ~510s vs ~2100s.
        indexed_spec_count = 0
        indexed_docs    = []   # successfully indexed docs (for status update later)
        pinecone_wait_tasks = []  # concurrent asyncio.Tasks — one per indexed doc

        for i, doc in enumerate(spec_docs, 1):

            # ── A: Parse + chunk + embed (sequential — uses shared db session) ──
            is_drawing = doc.file_type == "pdf_drawing"
            if is_drawing:
                project.pipeline_step = f"Analysing drawing sheets in {doc.original_filename} with Vision AI ({i}/{total_spec})…"
            else:
                project.pipeline_step = f"Parsing specification: {doc.original_filename} ({i}/{total_spec})…"
            doc.processing_status = "processing"
            db.commit()

            t_doc = _time.perf_counter()
            try:
                processor._process_specification_document(doc)
                doc.processing_status = "indexed"
                indexed_spec_count += 1
                indexed_docs.append(doc)
                db.commit()
                _pipeline_log.info(
                    f"[TIMING][PIPELINE] Parse+embed {doc.original_filename} "
                    f"({i}/{total_spec}): {_time.perf_counter() - t_doc:.2f}s"
                )
            except Exception as e:
                _pipeline_log.error(f"[PIPELINE] Parse failed {doc.original_filename}: {e}")
                doc.processing_status = "failed"
                doc.processing_error = str(e)[:1000]
                db.commit()
                continue  # skip this doc, continue to next

            # ── B: Fire Pinecone wait as a concurrent Task ────────────────────
            # _wait_for_pinecone_doc is purely async (asyncio.sleep + HTTP poll)
            # and does NOT touch the shared 'db' session — safe to overlap with
            # subsequent parses. Timeout raised to 180s for large documents.
            project.pipeline_step = f"Vectorising {doc.original_filename} into knowledge base ({i}/{total_spec})…"
            db.commit()
            wait_task = asyncio.create_task(
                _wait_for_pinecone_doc(str(project_id), str(doc.document_id), timeout=180)
            )
            pinecone_wait_tasks.append(wait_task)

        # ── Synchronization barrier: all Pinecone tasks must complete ─────────
        if pinecone_wait_tasks:
            project.pipeline_step = (
                f"Indexing {len(pinecone_wait_tasks)} document(s) into vector database…"
            )
            db.commit()
            t_wait_all = _time.perf_counter()
            await asyncio.gather(*pinecone_wait_tasks)
            _pipeline_log.info(
                f"[TIMING][PIPELINE] All Pinecone waits completed: "
                f"{_time.perf_counter() - t_wait_all:.2f}s"
            )

        # ── Phase 2: Extract ALL parameters ONCE after all docs are indexed ───
        # Running extraction once (not per-doc) reduces LLM calls by ~N× where
        # N is the number of spec documents, which is the main source of slowness.
        if indexed_spec_count > 0:
            project.pipeline_step = (
                f"Extracting {len(FACADE_PARAMETERS)} parameters across {indexed_spec_count} document(s)…"
            )
            db.commit()
            db.expire_all()

            extractor = ParameterExtractor(
                pinecone_index=pinecone_index,
                embedding_client=embedding_client,
                db_session=db,
                session_factory=SessionLocal,
            )
            t_extract = _time.perf_counter()
            extractions = await extractor.extract_all_parameters_async(
                str(project_id),
                facade_parameters=FACADE_PARAMETERS,
                max_concurrent=6,
                num_docs=indexed_spec_count,
            )
            found_count = len([e for e in extractions if e.get("found")])
            _pipeline_log.info(
                f"[TIMING][PIPELINE] Extraction (single pass, {indexed_spec_count} docs): "
                f"{_time.perf_counter() - t_extract:.2f}s — "
                f"{found_count}/{len(extractions)} params found"
            )
        else:
            _pipeline_log.warning("[PIPELINE] No spec docs indexed — skipping extraction")

        # Mark all successfully indexed docs as completed
        for doc in indexed_docs:
            doc.processing_status = "completed"
        db.commit()

        # ── All documents done ────────────────────────────────────────────────
        project.processing_status = "completed"
        project.pipeline_step = None
        project.processing_completed_at = datetime.now()
        db.commit()
        _pipeline_log.info(
            f"[TIMING][PIPELINE] TOTAL for project {project_id}: "
            f"{_time.perf_counter() - t_pipeline_start:.2f}s"
        )
        _pipeline_log.info(f"[PIPELINE] Completed for project {project_id}")

    except Exception as e:
        traceback.print_exc()
        _pipeline_log.error(f"[PIPELINE] FAILED for project {project_id}: {e}")
        try:
            project = db.query(Project).filter(Project.project_id == project_id).first()
            if project:
                project.processing_status = "failed"
                project.pipeline_step = None
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
        _timing_project_id.set(str(pid))
        _project_timings[str(pid)] = []   # reset on re-extract
        _pipeline_log.info(f"[RE-EXTRACT] Starting for project {pid}")
        _db = SessionLocal()
        try:
            # Mark as processing so the frontend polling detects it
            _proj = _db.query(Project).filter(Project.project_id == pid).first()
            if _proj:
                _proj.processing_status = "processing"
                _proj.pipeline_step = "Re-extracting parameters from all documents…"
                _db.commit()

            # Count already-processed spec docs so top_k / max_sources scale correctly
            spec_count = _db.query(Document).filter(
                Document.project_id == pid,
                Document.file_type.in_(['pdf_spec', 'docx_spec']),
                Document.processed == True,
            ).count()

            extractor = ParameterExtractor(
                pinecone_index=pinecone_index,
                embedding_client=embedding_client,
                db_session=_db,
                session_factory=SessionLocal,
            )
            extractions = await extractor.extract_all_parameters_async(
                str(pid),
                facade_parameters=FACADE_PARAMETERS,
                max_concurrent=5,
                num_docs=max(1, spec_count),
            )
            found_count = len([e for e in extractions if e.get("found")])
            _pipeline_log.info(f"[RE-EXTRACT] Done — {found_count}/{len(extractions)} parameters found")

            # Mark complete so polling stops
            _proj = _db.query(Project).filter(Project.project_id == pid).first()
            if _proj:
                _proj.processing_status = "completed"
                _proj.pipeline_step = None
                _db.commit()
        except Exception as e:
            import traceback as _tb
            _tb.print_exc()
            _pipeline_log.error(f"[RE-EXTRACT] FAILED for project {pid}: {e}")
            try:
                _proj = _db.query(Project).filter(Project.project_id == pid).first()
                if _proj:
                    _proj.processing_status = "failed"
                    _proj.pipeline_step = None
                    _db.commit()
            except Exception:
                pass
        finally:
            _db.close()

    background_tasks.add_task(_run_extraction, project_id)
    return {
        "project_id": str(project_id),
        "status": "re-extracting",
        "message": "Extraction started. Check /projects/{project_id}/parameters after ~60s.",
    }


@app.post("/projects/{project_id}/parameters/{param_name}/re-extract", status_code=202)
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

    async def _run_single_extraction(pid: uuid.UUID, p_config: dict):
        _timing_project_id.set(str(pid))
        _db = SessionLocal()
        try:
            spec_count = _db.query(Document).filter(
                Document.project_id == pid,
                Document.file_type.in_(['pdf_spec', 'docx_spec', 'pdf_drawing']),
                Document.processed == True,
            ).count()

            extractor = ParameterExtractor(
                pinecone_index=pinecone_index,
                embedding_client=embedding_client,
                db_session=_db,
                session_factory=SessionLocal,
            )
            loop = asyncio.get_running_loop()
            import time as _t
            t0 = _t.perf_counter()
            # Use focused single-param search
            focused_query = f"{p_config['display_name']} {' '.join(p_config['search_keywords'][:6])}"
            param_types = p_config.get('source_types') or None
            top_k = min(60, max(10, 4 * max(1, spec_count)))
            chunk_dicts = await extractor._search_pinecone_async(
                loop, focused_query, str(pid), top_k=top_k, file_types=param_types
            )
            if chunk_dicts:
                context = extractor._build_context(chunk_dicts, max_sources=min(20, len(chunk_dicts)))
                async with _get_llm_semaphore():
                    result_text = await asyncio.wait_for(
                        loop.run_in_executor(None, extractor._call_llm, p_config, context),
                        timeout=90.0,
                    )
                result = extractor._parse_llm_response(result_text, p_config, chunk_dicts)
            else:
                result = {'found': False, 'explanation': 'No relevant content found in indexed documents.'}

            extractor._store_extraction(str(pid), p_config, result)
            _pipeline_log.info(
                f"[SINGLE-EXTRACT] '{p_config['name']}' → "
                f"{'found ✓' if result.get('found') else 'not found'} "
                f"({_t.perf_counter()-t0:.2f}s)"
            )
        except Exception as e:
            _pipeline_log.error(f"[SINGLE-EXTRACT] Failed '{p_config['name']}': {e}")
        finally:
            _db.close()

    background_tasks.add_task(_run_single_extraction, project_id, param_config)
    return {
        "project_id": str(project_id),
        "parameter_name": param_name,
        "status": "re-extracting",
        "message": f"Re-extraction of '{param_config['display_name']}' started.",
    }


@app.get("/projects/{project_id}/parameters")
async def get_extracted_parameters(project_id: uuid.UUID, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    parameters = db.query(ExtractedParameter).filter(
        ExtractedParameter.project_id == project_id
    ).all()

    import json as _json
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
            # Source evidence text — shown in detail modal
            "source_text": chunk_text,
            # True when value was drawn from multiple documents (show multi-source badge)
            "multi_source": multi_source,
        })

    from models.document import Document as _Document
    docs = db.query(_Document).filter(_Document.project_id == project_id).all()
    documents_info = [
        {
            "document_id": str(d.document_id),
            "filename": d.original_filename,
            "file_type": d.file_type,
            "processing_status": getattr(d, 'processing_status', 'completed'),
            "page_count": getattr(d, 'page_count', None),
            "num_chunks": d.num_chunks,
        }
        for d in docs
    ]

    return {
        "project_id": str(project_id),
        "processing_status": project.processing_status,
        "pipeline_step": getattr(project, 'pipeline_step', None),
        "parameters": results,
        "total_extracted": len(results),
        "documents": documents_info,
    }


@app.get("/projects/{project_id}/timings")
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
    child_chunks = db.query(DocumentChunk).options(
        joinedload(DocumentChunk.document)
    ).filter(
        DocumentChunk.pinecone_id.in_(chunk_ids)
    ).all()

    # Hierarchical context expansion: prefer full section (parent) over fragment (child)
    parent_ids = [c.parent_chunk_id for c in child_chunks if c.parent_chunk_id]
    parent_map = {}
    if parent_ids:
        parent_rows = db.query(DocumentChunk).options(
            joinedload(DocumentChunk.document)
        ).filter(DocumentChunk.chunk_id.in_(parent_ids)).all()
        parent_map = {p.chunk_id: p for p in parent_rows}

    seen_parents: set = set()
    context_chunks = []
    for child in child_chunks:
        if child.parent_chunk_id and child.parent_chunk_id in parent_map:
            if child.parent_chunk_id not in seen_parents:
                seen_parents.add(child.parent_chunk_id)
                context_chunks.append(parent_map[child.parent_chunk_id])
        else:
            context_chunks.append(child)

    context = "\n\n".join([
        f"[Source {i+1}: {chunk.document.original_filename}, Page {chunk.page_number or 'N/A'}, "
        f"Section: {chunk.section_title or 'N/A'}]\n{chunk.chunk_text}"
        for i, chunk in enumerate(context_chunks[:3])
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
        num_sources_used=len(context_chunks),
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
            for chunk in context_chunks[:3]
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
