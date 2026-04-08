"""Add is_starred and is_archived to projects.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS is_starred BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    conn.execute(sa.text(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE projects DROP COLUMN IF EXISTS is_archived"))
    conn.execute(sa.text("ALTER TABLE projects DROP COLUMN IF EXISTS is_starred"))
