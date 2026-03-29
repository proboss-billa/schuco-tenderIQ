from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    String, Text, Integer, TIMESTAMP, ForeignKey
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from models.base import Base


class QueryLog(Base):
    __tablename__ = "query_log"

    query_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.project_id")
    )

    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_type: Mapped[str | None] = mapped_column(String(50))

    response_text: Mapped[str | None] = mapped_column(Text)

    response_time_ms: Mapped[int | None] = mapped_column(Integer)
    num_sources_used: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=func.now()
    )

    # Relationships
    project = relationship("Project", backref="query_logs")