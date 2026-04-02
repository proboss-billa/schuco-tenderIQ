from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    String, Text, Integer, TIMESTAMP, ForeignKey,
    UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from models.base import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=False,
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Hierarchy ────────────────────────────────────────────────────────────
    # 0 = section-level parent  (full section text, NOT indexed in Pinecone)
    # 1 = paragraph-level child (small slice, embedded & indexed in Pinecone)
    # Legacy chunks created before this feature default to 1 (child behaviour).
    chunk_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Back-reference to the section parent for level-1 children.
    # NULL for level-0 parents and for legacy chunks.
    parent_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_chunks.chunk_id", ondelete="SET NULL"),
        nullable=True,
    )

    # Doubly-linked list within the same section — enables "fetch next chunk"
    # traversal when a value spans a section boundary.
    prev_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    next_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # ── Content ──────────────────────────────────────────────────────────────
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)

    page_number:      Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_title:    Mapped[str | None] = mapped_column(Text, nullable=True)
    subsection_title: Mapped[str | None] = mapped_column(Text, nullable=True)

    # NULL for level-0 parent chunks (they are not in Pinecone)
    pinecone_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=func.now()
    )

    # ── Relationships ────────────────────────────────────────────────────────
    document = relationship("Document", backref="chunks")
    project  = relationship("Project",  backref="chunks")
    parent   = relationship(
        "DocumentChunk",
        remote_side="DocumentChunk.chunk_id",
        foreign_keys=[parent_chunk_id],
        backref="children",
    )

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_doc_chunk"),
        Index("idx_chunks_project",  "project_id"),
        Index("idx_chunks_pinecone", "pinecone_id"),
        Index("idx_chunks_parent",   "parent_chunk_id"),
        Index("idx_chunks_level",    "chunk_level"),
    )
