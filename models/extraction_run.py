"""Extraction run versioning model.

Each time parameter extraction runs for a project, a row is created here
to track timing, counts, and status.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    String, Text, Integer, Float, TIMESTAMP, ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from models.base import Base


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now()
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP, nullable=True
    )

    total_params: Mapped[int | None] = mapped_column(Integer, nullable=True)
    found_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    not_found_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pass1_found: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pass2_found: Mapped[int | None] = mapped_column(Integer, nullable=True)

    extraction_time_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20), default="running"
    )  # running, completed, failed

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    project = relationship("Project", backref="extraction_runs")

    __table_args__ = (
        Index("idx_extraction_runs_project", "project_id"),
    )
