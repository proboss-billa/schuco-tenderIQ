"""Add token_limit and tokens_used columns to users table.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_limit BIGINT NOT NULL DEFAULT 1000000"
    ))
    conn.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_used BIGINT NOT NULL DEFAULT 0"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS tokens_used"))
    conn.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS token_limit"))
