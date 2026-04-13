"""Delete all users (and cascaded data) except mike@sooru.ai and sthita@sooru.ai

One-time data cleanup. Projects are deleted first (CASCADE handles
documents, chunks, params, query_logs, boq_items, extraction_runs),
then the user rows themselves.

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

KEEP_EMAILS = ("mike@sooru.ai", "sthita@sooru.ai")


def upgrade() -> None:
    conn = op.get_bind()

    # Find user_ids to delete (everyone except the two we keep)
    placeholders = ", ".join(f":e{i}" for i in range(len(KEEP_EMAILS)))
    params = {f"e{i}": email for i, email in enumerate(KEEP_EMAILS)}

    rows = conn.execute(
        sa.text(f"SELECT user_id FROM users WHERE email NOT IN ({placeholders})"),
        params,
    ).fetchall()
    user_ids = [str(r[0]) for r in rows]

    if not user_ids:
        return  # nothing to delete

    # Get project_ids owned by those users (for logging)
    uid_placeholders = ", ".join(f":u{i}" for i in range(len(user_ids)))
    uid_params = {f"u{i}": uid for i, uid in enumerate(user_ids)}

    project_rows = conn.execute(
        sa.text(f"SELECT project_id FROM projects WHERE user_id IN ({uid_placeholders})"),
        uid_params,
    ).fetchall()
    project_ids = [str(r[0]) for r in project_rows]

    # Delete projects (CASCADE will handle documents, chunks, params,
    # query_logs, boq_items, extraction_runs)
    if project_ids:
        pid_placeholders = ", ".join(f":p{i}" for i in range(len(project_ids)))
        pid_params = {f"p{i}": pid for i, pid in enumerate(project_ids)}
        conn.execute(
            sa.text(f"DELETE FROM projects WHERE project_id IN ({pid_placeholders})"),
            pid_params,
        )

    # Delete the users
    conn.execute(
        sa.text(f"DELETE FROM users WHERE user_id IN ({uid_placeholders})"),
        uid_params,
    )

    # Clean up email_otps for deleted emails
    conn.execute(
        sa.text(f"DELETE FROM email_otps WHERE email NOT IN ({placeholders})"),
        params,
    )


def downgrade() -> None:
    # Data deletion is irreversible
    pass
