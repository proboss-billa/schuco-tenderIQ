"""Add extraction_runs table for extraction versioning.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS extraction_runs (
            run_id UUID PRIMARY KEY,
            project_id UUID REFERENCES projects(project_id) ON DELETE CASCADE,
            started_at TIMESTAMP DEFAULT now(),
            completed_at TIMESTAMP,
            total_params INTEGER,
            found_count INTEGER,
            not_found_count INTEGER,
            pass1_found INTEGER,
            pass2_found INTEGER,
            extraction_time_seconds FLOAT,
            status VARCHAR(20) DEFAULT 'running',
            error_message TEXT
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_extraction_runs_project ON extraction_runs(project_id)"
    ))


def downgrade() -> None:
    op.drop_table("extraction_runs")
