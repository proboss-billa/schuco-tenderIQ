from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import UUID
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