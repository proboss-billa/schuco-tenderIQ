from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    String, Text, Integer, TIMESTAMP, ForeignKey,
    UniqueConstraint, Index, DECIMAL
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from models.base import Base

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