"""Add avatar_path to users.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_path VARCHAR(500)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS avatar_path"))
