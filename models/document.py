from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, TIMESTAMP, Boolean, Integer, BigInteger, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
import uuid

from models.base import Base


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=False
    )

    original_filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False
    )

    file_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False
    )

    file_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True
    )

    file_path: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    uploaded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=func.now()
    )

    processed: Mapped[bool] = mapped_column(
        Boolean,
        default=False
    )

    num_chunks: Mapped[int] = mapped_column(
        Integer,
        default=0
    )

    processing_status: Mapped[str] = mapped_column(
        String(20),
        default="pending"
    )

    processing_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    page_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    project = relationship("Project", backref="documents")