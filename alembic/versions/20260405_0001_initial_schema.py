"""Initial schema — represents all tables as of the current codebase.

Revision ID: 0001
Revises: None
Create Date: 2026-04-05

This migration captures the full current schema so that Alembic has a
known baseline. Existing databases that already have these tables will
skip creation (``if_not_exists``-style checks via ``CREATE TABLE IF NOT
EXISTS`` semantics handled by op.create_table's implicit behavior, or
guarded explicitly below).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS so this is safe to run on an existing DB.
    conn = op.get_bind()

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS users (
            user_id UUID PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT now()
        )
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS projects (
            project_id UUID PRIMARY KEY,
            project_name VARCHAR(255) NOT NULL,
            project_description TEXT,
            project_type VARCHAR(20) NOT NULL DEFAULT 'commercial',
            created_at TIMESTAMP DEFAULT now(),
            updated_at TIMESTAMP DEFAULT now(),
            processing_status VARCHAR(50) DEFAULT 'uploaded',
            processing_started_at TIMESTAMP,
            processing_completed_at TIMESTAMP,
            error_message TEXT,
            pipeline_step TEXT
        )
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS documents (
            document_id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
            original_filename VARCHAR(500) NOT NULL,
            file_type VARCHAR(20) NOT NULL,
            file_size_bytes BIGINT,
            file_path TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT now(),
            processed BOOLEAN DEFAULT false,
            num_chunks INTEGER DEFAULT 0,
            processing_status VARCHAR(20) DEFAULT 'pending',
            processing_error TEXT,
            page_count INTEGER
        )
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            chunk_id UUID PRIMARY KEY,
            document_id UUID NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
            project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            chunk_level INTEGER NOT NULL DEFAULT 1,
            parent_chunk_id UUID REFERENCES document_chunks(chunk_id) ON DELETE SET NULL,
            prev_chunk_id UUID,
            next_chunk_id UUID,
            chunk_text TEXT NOT NULL,
            page_number INTEGER,
            section_title TEXT,
            subsection_title TEXT,
            pinecone_id VARCHAR(255),
            created_at TIMESTAMP DEFAULT now(),
            CONSTRAINT uq_doc_chunk UNIQUE (document_id, chunk_index)
        )
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS extracted_parameters (
            extraction_id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
            parameter_name VARCHAR(255) NOT NULL,
            parameter_display_name VARCHAR(255),
            value_text TEXT,
            value_numeric DECIMAL(15,3),
            unit VARCHAR(50),
            source_document_id UUID REFERENCES documents(document_id),
            source_page_number INTEGER,
            source_pages TEXT,
            source_section TEXT,
            source_subsection TEXT,
            source_chunk_id UUID REFERENCES document_chunks(chunk_id),
            confidence_score DECIMAL(3,2),
            extraction_method VARCHAR(50),
            validation_status VARCHAR(20) DEFAULT 'pending',
            notes TEXT,
            all_sources TEXT,
            created_at TIMESTAMP DEFAULT now(),
            CONSTRAINT uq_project_param UNIQUE (project_id, parameter_name)
        )
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS query_log (
            query_id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
            query_text TEXT NOT NULL,
            response_text TEXT,
            created_at TIMESTAMP DEFAULT now(),
            sources_json JSONB
        )
    """))

    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS boq_items (
            item_id UUID PRIMARY KEY,
            project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
            document_id UUID REFERENCES documents(document_id),
            item_description TEXT,
            quantity DECIMAL(15,3),
            unit VARCHAR(50),
            unit_rate DECIMAL(15,3),
            amount DECIMAL(15,3),
            section TEXT,
            raw_row_data TEXT,
            created_at TIMESTAMP DEFAULT now()
        )
    """))

    # Create indexes if they don't exist
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_extracted_project ON extracted_parameters(project_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_chunks_project ON document_chunks(project_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_chunks_pinecone ON document_chunks(pinecone_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_chunks_parent ON document_chunks(parent_chunk_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_chunks_level ON document_chunks(chunk_level)"
    ))


def downgrade() -> None:
    op.drop_table("boq_items")
    op.drop_table("query_log")
    op.drop_table("extracted_parameters")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("projects")
    op.drop_table("users")
