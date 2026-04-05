from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
import uuid

from models.base import Base


class Project(Base):
    __tablename__ = "projects"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    project_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    project_description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    # "commercial" or "residential"
    project_type: Mapped[str] = mapped_column(
        String(20),
        default="commercial",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=func.now()
    )

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=func.now(),
        onupdate=func.now(),
    )

    processing_status: Mapped[str] = mapped_column(
        String(50),
        default="uploaded"
    )

    processing_started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=True
    )

    processing_completed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=True
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    pipeline_step: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    # --- Streaming extraction flag (see alembic 0003) ---
    # When True, parameter extraction runs incrementally as documents finish
    # indexing instead of waiting for the entire corpus. New projects default
    # to True; legacy projects default to True at the DB level too, but the
    # coordinator still respects the flag for easy rollback.
    streaming_extraction: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=True,
    )

    # Number of incremental extraction passes completed for this project.
    # Used by the coordinator as a cost guard (hard cap).
    extraction_runs_completed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=True,
    )

    # --- Persisted coordinator state (alembic 0004) ---
    # Doc IDs the streaming extractor has already processed. Populated by
    # `ExtractionCoordinator` after each pass. Reloaded on worker restart so
    # the coordinator resumes where it left off instead of re-extracting.
    extracted_doc_ids: Mapped[list] = mapped_column(
        JSONB,
        default=list,
        nullable=True,
    )

    # Mapping of doc_id → file_type, used by the coordinator for priority
    # ordering (BoQ/spec before drawings). Kept in sync with indexed docs.
    doc_file_types: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=True,
    )