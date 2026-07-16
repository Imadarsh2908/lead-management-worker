"""
scripts/run_migrations.py
--------------------------
Applies pending Alembic migrations on container startup.

Bootstraps DBs that predate Alembic tracking: this app originally managed
its schema with Base.metadata.create_all() (still true for every table
except `users` — see app/core/database.create_all_tables), so a database
can already have the exact schema migration 0001 would create, but no
alembic_version row recording that. Running `alembic upgrade head` there
fails with "relation already exists" (0001 tries to CREATE TABLE users
again). If we detect that shape — no alembic_version table, but `users`
already exists — we stamp 0001 first (record the revision without
re-running its SQL), then upgrade normally.

A genuinely fresh database (no alembic_version, no users table) just runs
every migration from scratch as usual.
"""
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.core.database import engine


def main() -> None:
    cfg = Config("alembic.ini")
    inspector = inspect(engine)

    if not inspector.has_table("alembic_version") and inspector.has_table("users"):
        command.stamp(cfg, "0001")

    command.upgrade(cfg, "head")


if __name__ == "__main__":
    main()
