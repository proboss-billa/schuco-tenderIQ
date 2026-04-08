"""Auth, upload, and deletion schema changes.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-08

- Add name and phone columns to users table
- Add user_id FK to projects (nullable for existing rows)
- Fix extracted_parameters.source_document_id FK to ON DELETE SET NULL
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Users: add name and phone ────────────────────────────────────────
    conn.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS name VARCHAR(255)"
    ))
    conn.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(50)"
    ))

    # ── Projects: add user_id FK (nullable for existing rows) ────────────
    conn.execute(sa.text(
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(user_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)"
    ))

    # ── Fix extracted_parameters.source_document_id FK to SET NULL ───────
    # Drop the old constraint (no ON DELETE) and re-add with SET NULL
    conn.execute(sa.text("""
        DO $$
        DECLARE fk_name TEXT;
        BEGIN
            SELECT constraint_name INTO fk_name
            FROM information_schema.table_constraints
            WHERE table_name = 'extracted_parameters'
              AND constraint_type = 'FOREIGN KEY'
              AND constraint_name LIKE '%source_document_id%';

            IF fk_name IS NOT NULL THEN
                EXECUTE 'ALTER TABLE extracted_parameters DROP CONSTRAINT ' || fk_name;
            END IF;
        END $$;
    """))
    conn.execute(sa.text("""
        ALTER TABLE extracted_parameters
        ADD CONSTRAINT fk_extracted_params_source_doc
        FOREIGN KEY (source_document_id)
        REFERENCES documents(document_id)
        ON DELETE SET NULL
    """))


def downgrade() -> None:
    conn = op.get_bind()

    # Revert source_document_id FK
    conn.execute(sa.text(
        "ALTER TABLE extracted_parameters DROP CONSTRAINT IF EXISTS fk_extracted_params_source_doc"
    ))
    conn.execute(sa.text("""
        ALTER TABLE extracted_parameters
        ADD CONSTRAINT extracted_parameters_source_document_id_fkey
        FOREIGN KEY (source_document_id)
        REFERENCES documents(document_id)
    """))

    conn.execute(sa.text("DROP INDEX IF EXISTS idx_projects_user_id"))
    conn.execute(sa.text("ALTER TABLE projects DROP COLUMN IF EXISTS user_id"))
    conn.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS phone"))
    conn.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS name"))
