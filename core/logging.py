import contextvars
import logging as _logging_mod
import re as _re
import time as _time_mod
import uuid as _uuid_mod

# ── Per-project timing store ──────────────────────────────────────────────────
# Keyed by project_id string -> list of timing dicts.
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
    "BOQ_TEXT_BUILD": "BOQ text prepared",
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

# Pipeline logger used by services
_pipeline_log = _logging_mod.getLogger("pipeline")


# ── Request-ID structured logging ────────────────────────────────────────────

# ContextVar holding the current request ID (set by RequestIDMiddleware).
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class _RequestIDFormatter(_logging_mod.Formatter):
    """Log formatter that injects [req_id] into every message."""

    def format(self, record: _logging_mod.LogRecord) -> str:
        record.request_id = request_id_var.get("-")
        return super().format(record)


def get_logger(name: str) -> _logging_mod.Logger:
    """Return a logger configured with request-ID aware formatting.

    Usage::

        from core.logging import get_logger
        logger = get_logger(__name__)
        logger.info("Processing started")  # => "2026-04-05 09:00:00 [ab12cd34] INFO processing.x - Processing started"
    """
    lgr = _logging_mod.getLogger(name)
    if not lgr.handlers:
        handler = _logging_mod.StreamHandler()
        formatter = _RequestIDFormatter(
            fmt="%(asctime)s [%(request_id)s] %(levelname)s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        lgr.addHandler(handler)
        lgr.setLevel(_logging_mod.INFO)
    return lgr
