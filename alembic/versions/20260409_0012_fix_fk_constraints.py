"""Fix FK constraints: add ON DELETE SET NULL / CASCADE for safe document deletion

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # extracted_parameters.source_chunk_id -> ON DELETE SET NULL
    conn.execute(sa.text(
        "ALTER TABLE extracted_parameters "
        "DROP CONSTRAINT IF EXISTS extracted_parameters_source_chunk_id_fkey"
    ))
    conn.execute(sa.text(
        "ALTER TABLE extracted_parameters "
        "ADD CONSTRAINT extracted_parameters_source_chunk_id_fkey "
        "FOREIGN KEY (source_chunk_id) REFERENCES document_chunks(chunk_id) ON DELETE SET NULL"
    ))

    # extracted_parameters.source_document_id -> ON DELETE SET NULL
    conn.execute(sa.text(
        "ALTER TABLE extracted_parameters "
        "DROP CONSTRAINT IF EXISTS extracted_parameters_source_document_id_fkey"
    ))
    conn.execute(sa.text(
        "ALTER TABLE extracted_parameters "
        "ADD CONSTRAINT extracted_parameters_source_document_id_fkey "
        "FOREIGN KEY (source_document_id) REFERENCES documents(document_id) ON DELETE SET NULL"
    ))

    # boq_items.document_id -> ON DELETE CASCADE
    conn.execute(sa.text(
        "ALTER TABLE boq_items "
        "DROP CONSTRAINT IF EXISTS boq_items_document_id_fkey"
    ))
    conn.execute(sa.text(
        "ALTER TABLE boq_items "
        "ADD CONSTRAINT boq_items_document_id_fkey "
        "FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE"
    ))


def downgrade() -> None:
    conn = op.get_bind()

    # Revert source_chunk_id to bare FK (no ON DELETE)
    conn.execute(sa.text(
        "ALTER TABLE extracted_parameters "
        "DROP CONSTRAINT IF EXISTS extracted_parameters_source_chunk_id_fkey"
    ))
    conn.execute(sa.text(
        "ALTER TABLE extracted_parameters "
        "ADD CONSTRAINT extracted_parameters_source_chunk_id_fkey "
        "FOREIGN KEY (source_chunk_id) REFERENCES document_chunks(chunk_id)"
    ))

    # Revert source_document_id to bare FK
    conn.execute(sa.text(
        "ALTER TABLE extracted_parameters "
        "DROP CONSTRAINT IF EXISTS extracted_parameters_source_document_id_fkey"
    ))
    conn.execute(sa.text(
        "ALTER TABLE extracted_parameters "
        "ADD CONSTRAINT extracted_parameters_source_document_id_fkey "
        "FOREIGN KEY (source_document_id) REFERENCES documents(document_id)"
    ))

    # Revert boq_items.document_id to bare FK
    conn.execute(sa.text(
        "ALTER TABLE boq_items "
        "DROP CONSTRAINT IF EXISTS boq_items_document_id_fkey"
    ))
    conn.execute(sa.text(
        "ALTER TABLE boq_items "
        "ADD CONSTRAINT boq_items_document_id_fkey "
        "FOREIGN KEY (document_id) REFERENCES documents(document_id)"
    ))
