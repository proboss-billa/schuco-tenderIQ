"""Alembic environment configuration.

Reads DATABASE_URL from environment (same source as core.database) and
configures SQLAlchemy metadata from the project's models so that
``alembic revision --autogenerate`` can diff against the live schema.
"""

import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

load_dotenv()

# ── Alembic Config object ───────────────────────────────────────────────────
config = context.config

# Interpret the config file for Python logging if present
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Import ALL models so Base.metadata is fully populated ────────────────────
from models.base import Base                    # noqa: E402
import models.project                           # noqa: E402, F401
import models.document                          # noqa: E402, F401
import models.document_chunk                    # noqa: E402, F401
import models.extracted_parameter               # noqa: E402, F401
import models.user                              # noqa: E402, F401
import models.query_log                         # noqa: E402, F401
import models.boq_item                          # noqa: E402, F401
import models.extraction_run                    # noqa: E402, F401

target_metadata = Base.metadata

# ── Database URL from environment ────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://poc_user:poc_password@localhost:5432/tender_poc",
)
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emits SQL to stdout."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to the database."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = DATABASE_URL

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
