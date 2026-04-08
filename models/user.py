import uuid
from datetime import datetime

from sqlalchemy import BigInteger, String, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    phone: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True
    )

    avatar_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )

    token_limit: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=1_000_000,
        server_default="1000000",
    )

    tokens_used: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=func.now()
    )