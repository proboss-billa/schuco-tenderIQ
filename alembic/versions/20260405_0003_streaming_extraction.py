"""⚠️  PARKED EXPERIMENT — migration for the parked streaming-extraction
    work. Safe to leave: alembic won't auto-run unless explicitly invoked
    via `alembic upgrade head`. Do not delete.  ⚠️

Add lifecycle state columns for streaming parameter extraction.

Adds per-parameter lifecycle tracking so incremental extraction passes can
decide when to re-extract, when to promote a value to stable/final, and
expose a history of value changes to the UI.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # --- extracted_parameters: lifecycle tracking ---
    conn.execute(sa.text("""
        ALTER TABLE extracted_parameters
        ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(20) DEFAULT 'tentative'
    """))
    conn.execute(sa.text("""
        ALTER TABLE extracted_parameters
        ADD COLUMN IF NOT EXISTS last_changed_at TIMESTAMP
    """))
    conn.execute(sa.text("""
        ALTER TABLE extracted_parameters
        ADD COLUMN IF NOT EXISTS evidence_fingerprint VARCHAR(64)
    """))
    conn.execute(sa.text("""
        ALTER TABLE extracted_parameters
        ADD COLUMN IF NOT EXISTS change_count INTEGER DEFAULT 0
    """))
    conn.execute(sa.text("""
        ALTER TABLE extracted_parameters
        ADD COLUMN IF NOT EXISTS history TEXT
    """))

    # Any rows already present (from legacy non-streaming runs) are by
    # definition final — they were written after full indexing.
    conn.execute(sa.text("""
        UPDATE extracted_parameters
        SET lifecycle_status = 'final'
        WHERE lifecycle_status IS NULL OR lifecycle_status = 'tentative'
    """))

    # --- projects: feature flag + incremental run counter ---
    conn.execute(sa.text("""
        ALTER TABLE projects
        ADD COLUMN IF NOT EXISTS streaming_extraction BOOLEAN DEFAULT TRUE
    """))
    conn.execute(sa.text("""
        ALTER TABLE projects
        ADD COLUMN IF NOT EXISTS extraction_runs_completed INTEGER DEFAULT 0
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE projects DROP COLUMN IF EXISTS extraction_runs_completed"))
    conn.execute(sa.text("ALTER TABLE projects DROP COLUMN IF EXISTS streaming_extraction"))
    conn.execute(sa.text("ALTER TABLE extracted_parameters DROP COLUMN IF EXISTS history"))
    conn.execute(sa.text("ALTER TABLE extracted_parameters DROP COLUMN IF EXISTS change_count"))
    conn.execute(sa.text("ALTER TABLE extracted_parameters DROP COLUMN IF EXISTS evidence_fingerprint"))
    conn.execute(sa.text("ALTER TABLE extracted_parameters DROP COLUMN IF EXISTS last_changed_at"))
    conn.execute(sa.text("ALTER TABLE extracted_parameters DROP COLUMN IF EXISTS lifecycle_status"))
