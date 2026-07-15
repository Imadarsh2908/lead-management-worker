"""
app/core/database.py
---------------------
Initializes the SQLAlchemy engine and session factory.
Uses the DATABASE_URL from the central config.

SessionLocal is a factory — call it to get a new DB session.
The `get_db` dependency (in api/dependencies.py) uses this to
yield sessions within FastAPI request lifecycles.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.config import settings


# ── Engine ─────────────────────────────────────────────────
# pool_pre_ping=True: tests connections before using them, discarding stale ones.
# This prevents "Connection closed" errors after periods of inactivity.
if settings.DATABASE_URL.startswith("sqlite"):
    # DEMO/TEST SEAM (env-guarded by the URL scheme; inert for the default
    # Postgres URL): allow a zero-dependency SQLite backend so the demo harness
    # can run a real HTTP server with no Postgres. check_same_thread=False lets
    # FastAPI BackgroundTasks (which run in a worker thread) touch the DB.
    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,       # Max persistent connections in the pool
        max_overflow=20,    # Extra connections allowed beyond pool_size under load
    )

# ── Session Factory ────────────────────────────────────────
# autocommit=False: we control commits explicitly for transactional safety.
# autoflush=False: prevents premature DB writes before we intend to commit.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── Declarative Base ───────────────────────────────────────
# All SQLAlchemy models inherit from this Base.
# This is the single source of truth for Alembic migrations.
class Base(DeclarativeBase):
    pass


def create_all_tables():
    """
    Creates all database tables based on SQLAlchemy model metadata.
    Called on application startup. In production, prefer Alembic migrations.
    """
    # Import models here so Base.metadata is populated before create_all()
    from app.models import lead  # noqa: F401
    Base.metadata.create_all(bind=engine)
