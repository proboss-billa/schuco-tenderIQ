import logging
from sqlalchemy import text

from core.database import engine
from models.base import Base

# NOTE: Alembic is now the preferred migration tool. See alembic/ directory
# and run ``alembic upgrade head`` for new deployments. This file is kept as
# a fallback for environments where Alembic is not yet configured.

logger = logging.getLogger("tenderiq.migrations")


def create_tables():
    """Create all tables from SQLAlchemy models."""
    Base.metadata.create_all(bind=engine)


def _run_migration(conn, name: str, sql: str):
    """Run a single migration statement with error isolation."""
    try:
        conn.execute(text(sql))
        conn.commit()
    except Exception as e:
        logger.warning(f"Migration '{name}' failed (may already be applied): {e}")
        conn.rollback()


def run_migrations():
    """Run all ALTER TABLE migrations introduced after initial schema creation.

    Each statement is wrapped individually so one failure doesn't block the rest.
    """
    with engine.connect() as conn:
        # ── Extracted parameters columns ─────────────────────────────────────
        _run_migration(conn, "source_pages column",
            "ALTER TABLE extracted_parameters ADD COLUMN IF NOT EXISTS source_pages TEXT"
        )

        # ── Hierarchical chunking columns (parent-child chunk strategy) ──────
        _run_migration(conn, "chunk_level column",
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS "
            "chunk_level INTEGER NOT NULL DEFAULT 1"
        )
        _run_migration(conn, "parent_chunk_id column",
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS "
            "parent_chunk_id UUID REFERENCES document_chunks(chunk_id) ON DELETE SET NULL"
        )
        _run_migration(conn, "prev_chunk_id column",
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS prev_chunk_id UUID"
        )
        _run_migration(conn, "next_chunk_id column",
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS next_chunk_id UUID"
        )
        _run_migration(conn, "pinecone_id nullable",
            "ALTER TABLE document_chunks ALTER COLUMN pinecone_id DROP NOT NULL"
        )

        # ── Multi-file / large-file support ──────────────────────────────────
        _run_migration(conn, "documents processing_status",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS processing_status VARCHAR(20) DEFAULT 'pending'"
        )
        _run_migration(conn, "documents processing_error",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS processing_error TEXT"
        )
        _run_migration(conn, "documents page_count",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_count INTEGER"
        )
        _run_migration(conn, "extracted_parameters all_sources",
            "ALTER TABLE extracted_parameters ADD COLUMN IF NOT EXISTS all_sources TEXT"
        )
        _run_migration(conn, "projects pipeline_step",
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS pipeline_step TEXT"
        )

        # ── Sources JSON in query_log ────────────────────────────────────────
        _run_migration(conn, "query_log sources_json",
            "ALTER TABLE query_log ADD COLUMN IF NOT EXISTS sources_json JSONB"
        )

        # ── Project type and updated_at ──────────────────────────────────────
        _run_migration(conn, "projects project_type",
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
            "project_type VARCHAR(20) NOT NULL DEFAULT 'commercial'"
        )
        _run_migration(conn, "projects updated_at",
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
            "updated_at TIMESTAMP DEFAULT now()"
        )

        # ── Project error_message column ─────────────────────────────────────
        _run_migration(conn, "projects error_message",
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS error_message TEXT"
        )

        # ── Extraction runs table for versioning ─────────────────────────────
        _run_migration(conn, "extraction_runs table", """
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
        """)
        _run_migration(conn, "extraction_runs index",
            "CREATE INDEX IF NOT EXISTS idx_extraction_runs_project "
            "ON extraction_runs(project_id)"
        )

        # ── Streaming extraction lifecycle fields (alembic 0003) ─────────────
        _run_migration(conn, "extracted_parameters lifecycle_status",
            "ALTER TABLE extracted_parameters ADD COLUMN IF NOT EXISTS "
            "lifecycle_status VARCHAR(20) DEFAULT 'tentative'"
        )
        _run_migration(conn, "extracted_parameters last_changed_at",
            "ALTER TABLE extracted_parameters ADD COLUMN IF NOT EXISTS "
            "last_changed_at TIMESTAMP"
        )
        _run_migration(conn, "extracted_parameters evidence_fingerprint",
            "ALTER TABLE extracted_parameters ADD COLUMN IF NOT EXISTS "
            "evidence_fingerprint VARCHAR(64)"
        )
        _run_migration(conn, "extracted_parameters change_count",
            "ALTER TABLE extracted_parameters ADD COLUMN IF NOT EXISTS "
            "change_count INTEGER DEFAULT 0"
        )
        _run_migration(conn, "extracted_parameters history",
            "ALTER TABLE extracted_parameters ADD COLUMN IF NOT EXISTS history TEXT"
        )
        # Legacy rows (from pre-streaming runs) are by definition final.
        _run_migration(conn, "extracted_parameters legacy final",
            "UPDATE extracted_parameters SET lifecycle_status = 'final' "
            "WHERE lifecycle_status IS NULL"
        )
        _run_migration(conn, "projects streaming_extraction",
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
            "streaming_extraction BOOLEAN DEFAULT TRUE"
        )
        _run_migration(conn, "projects extraction_runs_completed",
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
            "extraction_runs_completed INTEGER DEFAULT 0"
        )
