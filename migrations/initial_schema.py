from sqlalchemy import text

from core.database import engine
from models.base import Base

# NOTE: Alembic is now the preferred migration tool. See alembic/ directory
# and run ``alembic upgrade head`` for new deployments. This file is kept as
# a fallback for environments where Alembic is not yet configured.


def create_tables():
    """Create all tables from SQLAlchemy models."""
    Base.metadata.create_all(bind=engine)


def run_migrations():
    """Run all ALTER TABLE migrations introduced after initial schema creation."""
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE extracted_parameters ADD COLUMN IF NOT EXISTS source_pages TEXT"
        ))
        # ── Hierarchical chunking columns (parent-child chunk strategy) ───────
        # chunk_level: 0=section parent (not in Pinecone), 1=child (in Pinecone)
        conn.execute(text(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS "
            "chunk_level INTEGER NOT NULL DEFAULT 1"
        ))
        # parent_chunk_id: level-1 children reference their level-0 section parent
        conn.execute(text(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS "
            "parent_chunk_id UUID REFERENCES document_chunks(chunk_id) ON DELETE SET NULL"
        ))
        # prev/next links for in-section traversal
        conn.execute(text(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS prev_chunk_id UUID"
        ))
        conn.execute(text(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS next_chunk_id UUID"
        ))
        # parent chunks have no Pinecone ID -- make the column nullable
        conn.execute(text(
            "ALTER TABLE document_chunks ALTER COLUMN pinecone_id DROP NOT NULL"
        ))
        conn.commit()
        # ── New columns for multi-file / large-file support ───────────────────
        conn.execute(text(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS processing_status VARCHAR(20) DEFAULT 'pending'"
        ))
        conn.execute(text(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS processing_error TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_count INTEGER"
        ))
        conn.execute(text(
            "ALTER TABLE extracted_parameters ADD COLUMN IF NOT EXISTS all_sources TEXT"
        ))
        conn.execute(text(
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS pipeline_step TEXT"
        ))
        # ── Sources JSON in query_log for chat history persistence ────────────
        conn.execute(text(
            "ALTER TABLE query_log ADD COLUMN IF NOT EXISTS sources_json JSONB"
        ))
        # ── Project type (commercial / residential) and updated_at ────────────
        conn.execute(text(
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
            "project_type VARCHAR(20) NOT NULL DEFAULT 'commercial'"
        ))
        conn.execute(text(
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS "
            "updated_at TIMESTAMP DEFAULT now()"
        ))
        conn.commit()
        # ── Extraction runs table for versioning ─────────────────────────────
        conn.execute(text("""
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
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_extraction_runs_project "
            "ON extraction_runs(project_id)"
        ))
        conn.commit()
