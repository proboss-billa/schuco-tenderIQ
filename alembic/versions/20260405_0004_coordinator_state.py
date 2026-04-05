"""Persist streaming-extraction coordinator state across worker restarts.

Adds two JSONB columns on `projects` so a worker restart in the middle of
an extraction pipeline doesn't lose track of which docs have already been
extracted. This prevents duplicate-work waste after a crash.

- `extracted_doc_ids`  : JSONB array of document UUIDs the extractor has
                         already processed.
- `doc_file_types`     : JSONB object mapping doc_id → file_type string,
                         used by the coordinator for priority ordering.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE projects
        ADD COLUMN IF NOT EXISTS extracted_doc_ids JSONB DEFAULT '[]'::jsonb
    """))
    conn.execute(sa.text("""
        ALTER TABLE projects
        ADD COLUMN IF NOT EXISTS doc_file_types JSONB DEFAULT '{}'::jsonb
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE projects DROP COLUMN IF EXISTS doc_file_types"))
    conn.execute(sa.text("ALTER TABLE projects DROP COLUMN IF EXISTS extracted_doc_ids"))
