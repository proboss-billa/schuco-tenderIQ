from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Iterable

from sqlalchemy import (
    String, Text, Integer, TIMESTAMP, ForeignKey,
    UniqueConstraint, Index, DECIMAL
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from models.base import Base

# Lifecycle status values for streaming extraction.
#   tentative — value extracted but indexing is still in progress; may change
#   stable    — value unchanged across multiple incremental passes
#   final     — corpus fully indexed, last extraction pass complete
#   conflict  — value has flipped more than twice; needs human review
LIFECYCLE_TENTATIVE = "tentative"
LIFECYCLE_STABLE = "stable"
LIFECYCLE_FINAL = "final"
LIFECYCLE_CONFLICT = "conflict"

# How many entries of value-change history to keep per parameter (FIFO).
HISTORY_MAX_ENTRIES = 5
# A parameter that flips this many times is marked `conflict`.
CONFLICT_CHANGE_THRESHOLD = 2

class ExtractedParameter(Base):
    __tablename__ = "extracted_parameters"

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=False
    )

    parameter_name: Mapped[str] = mapped_column(String(255), nullable=False)
    parameter_display_name: Mapped[str | None] = mapped_column(String(255))

    value_text: Mapped[str | None] = mapped_column(Text)
    value_numeric: Mapped[float | None] = mapped_column(DECIMAL(15, 3))
    unit: Mapped[str | None] = mapped_column(String(50))

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.document_id")
    )

    source_page_number: Mapped[int | None] = mapped_column(Integer)
    source_pages: Mapped[str | None] = mapped_column(Text)  # JSON array of all page numbers
    source_section: Mapped[str | None] = mapped_column(Text)
    source_subsection: Mapped[str | None] = mapped_column(Text)

    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_chunks.chunk_id")
    )

    confidence_score: Mapped[float | None] = mapped_column(DECIMAL(3, 2))
    extraction_method: Mapped[str | None] = mapped_column(String(50))

    validation_status: Mapped[str] = mapped_column(
        String(20),
        default="pending"
    )

    notes: Mapped[str | None] = mapped_column(Text)

    all_sources: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )  # JSON: [{"document_id": "...", "document": "file.pdf", "page": 12, "pages": [12, 15], "section": "..."}]

    # --- Streaming extraction lifecycle fields (see alembic 0003) ---
    # Current lifecycle state: tentative | stable | final | conflict
    lifecycle_status: Mapped[str] = mapped_column(
        String(20),
        default=LIFECYCLE_TENTATIVE,
        nullable=True,
    )
    # Timestamp of the last time the *value* changed (not every re-extraction).
    last_changed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP, nullable=True)
    # SHA256 of the sorted chunk_ids that fed the last extraction. If this
    # hasn't changed since last pass, there's no point re-extracting.
    evidence_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # How many times the value has flipped. Used to detect flip-flopping.
    change_count: Mapped[int] = mapped_column(Integer, default=0, nullable=True)
    # JSON array of up to HISTORY_MAX_ENTRIES prior values:
    #   [{"value": "...", "confidence": 0.9, "sources": [...], "at": "2026-..."}]
    history: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=func.now()
    )

    # Relationships
    project = relationship("Project", backref="extracted_parameters")
    source_document = relationship("Document")
    source_chunk = relationship("DocumentChunk")

    __table_args__ = (
        UniqueConstraint("project_id", "parameter_name", name="uq_project_param"),
        Index("idx_extracted_project", "project_id"),
    )

    # ---------- Lifecycle helpers ----------

    def mark_tentative(self) -> None:
        self.lifecycle_status = LIFECYCLE_TENTATIVE

    def mark_stable(self) -> None:
        self.lifecycle_status = LIFECYCLE_STABLE

    def mark_final(self) -> None:
        self.lifecycle_status = LIFECYCLE_FINAL

    def mark_conflict(self) -> None:
        self.lifecycle_status = LIFECYCLE_CONFLICT

    def append_history(self, value, confidence, sources) -> None:
        """Push the previous value onto the history stack (FIFO, max N)."""
        try:
            existing = json.loads(self.history) if self.history else []
        except (ValueError, TypeError):
            existing = []
        existing.append({
            "value": value,
            "confidence": float(confidence) if confidence is not None else None,
            "sources": sources,
            "at": datetime.utcnow().isoformat() + "Z",
        })
        # Keep only the most recent N entries.
        self.history = json.dumps(existing[-HISTORY_MAX_ENTRIES:])

    @staticmethod
    def compute_fingerprint(chunk_ids: Iterable) -> str:
        """Stable SHA256 of the sorted chunk id set that fed an extraction.

        Accepts UUIDs or strings. An empty set yields a deterministic hash so
        callers can still compare against it.
        """
        ids = sorted(str(c) for c in chunk_ids)
        joined = "|".join(ids)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()