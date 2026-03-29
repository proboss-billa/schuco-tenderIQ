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

class BOQItem(Base):
    __tablename__ = "boq_items"

    boq_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=False
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False
    )

    item_number: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)

    quantity: Mapped[float | None] = mapped_column(DECIMAL(15, 3))
    unit: Mapped[str | None] = mapped_column(String(50))
    rate: Mapped[float | None] = mapped_column(DECIMAL(15, 2))
    amount: Mapped[float | None] = mapped_column(DECIMAL(15, 2))

    category: Mapped[str | None] = mapped_column(String(255))
    sub_category: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=func.now()
    )

    # Relationships
    project = relationship("Project", backref="boq_items")
    document = relationship("Document", backref="boq_items")

    __table_args__ = (
        Index("idx_boq_project", "project_id"),
    )