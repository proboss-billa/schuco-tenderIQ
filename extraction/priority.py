"""
⚠️  PARKED EXPERIMENT — NOT ACTIVE AS OF 2026-04-05. Kept in-tree for
    later resumption of the streaming-extraction work. Do not delete.  ⚠️

priority.py
───────────
Document-type priority table used as a tiebreaker during incremental
parameter extraction.

When a new extraction pass produces a *different* value for a parameter
that already has an answer, we need a merge rule. The primary rule is
confidence (higher wins by at least 0.05). When confidences tie, we fall
back to this priority — a value sourced from a BoQ beats one sourced
from a general spec, which beats one from a drawing annotation, etc.

The numbers are purely ordinal. Tune freely without breaking callers.
"""
from __future__ import annotations

# Higher number = stronger authority for reconciling conflicts.
DOC_PRIORITY: dict[str, int] = {
    "excel_boq":    100,   # BoQ is the contractual source of truth
    "pdf_spec":      80,
    "docx_spec":     80,
    "pdf_drawing":   60,
    "dwg_drawing":   60,
    "dxf_drawing":   60,
    "other":         10,
}


def priority_for(file_type: str | None) -> int:
    """Return priority for a Document.file_type, defaulting to `other`."""
    if not file_type:
        return DOC_PRIORITY["other"]
    return DOC_PRIORITY.get(file_type, DOC_PRIORITY["other"])
