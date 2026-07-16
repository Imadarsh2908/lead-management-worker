"""
alembic/env.py
---------------
Alembic migration environment configuration.

Wires the production DATABASE_URL from settings and imports all ORM models
so Alembic can detect changes and auto-generate migration scripts.

Usage:
  # Generate a migration after editing models/lead.py:
  alembic revision --autogenerate -m "add_company_size_to_leads"

  # Apply pending migrations to the database:
  alembic upgrade head

  # Rollback one migration:
  alembic downgrade -1
"""
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# ── Import your app's Settings and Base ───────────────────────────────────
from app.core.config import settings
from app.core.database import Base

# ── Import ALL models so Alembic can detect their tables ──────────────────
# If you add a new model file, import it here too.
from app.models import lead, user  # noqa: F401

# ── Alembic Config object ─────────────────────────────────────────────────
config = context.config

# Override the sqlalchemy.url from alembic.ini with the value from settings.
# This ensures we always use the same DATABASE_URL as the application.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Set up Python logging if alembic.ini has a [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object that Alembic will inspect for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    In offline mode, Alembic generates the SQL script without connecting
    to a database. Useful for reviewing migrations before applying them,
    or for applying them via a DBA.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.

    In online mode, Alembic connects to the database directly and applies
    each migration within a transaction (with automatic rollback on failure).
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
