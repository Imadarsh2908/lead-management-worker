"""
app/core/memory.py
-------------------
Provides the LangGraph checkpointer — the "where was I in the pipeline" store.

How it works:
  - After EVERY node in the LangGraph StateGraph executes, the full AgentState is
    serialized and saved to the checkpoint store, keyed by the workflow_id
    (used as LangGraph's thread_id).
  - If the server crashes mid-workflow, invoking the graph again with the SAME
    workflow_id resumes from the LAST SUCCESSFUL checkpoint instead of re-running
    completed nodes (no duplicate LLM calls / tool invocations).

Backend selection (get_checkpointer), in priority order:
  1. Redis Stack (REDIS_URL) → RedisSaver. Preferred: purpose-built for this,
     scales across worker processes. Requires the Search/JSON modules (Redis Stack).
  2. PostgreSQL (DATABASE_URL is a postgres URL) → PostgresSaver. Durable fallback
     that reuses the app's own database — no extra service.
  3. In-memory (InMemorySaver). Used in the test environment and as the last-resort
     fallback. NOT crash-safe, but keeps dev/test runs dependency-free.

Each backend degrades to the next on any failure, so the app always starts.
"""
import redis
from loguru import logger

from app.core.config import settings

# Module-level handle so the psycopg connection pool lives for the process
# lifetime (the checkpointer is built once as a singleton in graph.py).
_pg_pool = None


def get_redis_client() -> redis.Redis:
    """Creates a pooled Redis client from REDIS_URL with fast-fail timeouts."""
    pool = redis.ConnectionPool.from_url(
        settings.REDIS_URL,
        max_connections=20,
        decode_responses=False,       # Keep as bytes for LangGraph serialization
        socket_connect_timeout=2,     # fail fast → fall back instead of hanging
        socket_timeout=2,
    )
    return redis.Redis(connection_pool=pool)


def _pg_conninfo(database_url: str) -> str:
    """
    Normalize a SQLAlchemy-style URL to a libpq conninfo psycopg accepts.
    e.g. 'postgresql+psycopg2://u:p@h/db' -> 'postgresql://u:p@h/db'.
    """
    if "+" in database_url.split("://", 1)[0]:
        scheme, rest = database_url.split("://", 1)
        database_url = scheme.split("+", 1)[0] + "://" + rest
    return database_url


def _in_memory():
    from langgraph.checkpoint.memory import InMemorySaver
    return InMemorySaver()


def _try_redis():
    """Redis Stack checkpointer, or None if unreachable / missing modules."""
    try:
        from langgraph.checkpoint.redis import RedisSaver
        client = get_redis_client()
        client.ping()  # fail fast if the server isn't there
        checkpointer = RedisSaver(redis_client=client)
        checkpointer.setup()  # provisions RediSearch/JSON indexes (needs Redis Stack)
        logger.info("Redis checkpointer initialized (durable crash recovery via Redis Stack).")
        return checkpointer
    except Exception as e:  # noqa: BLE001 — degrade to the next backend
        logger.warning(f"Redis checkpointer unavailable ({type(e).__name__}: {str(e)[:100]}).")
        return None


def _try_postgres():
    """PostgresSaver checkpointer, or None if DATABASE_URL isn't Postgres / on error."""
    if not settings.DATABASE_URL.startswith("postgres"):
        return None
    global _pg_pool
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg_pool import ConnectionPool
        from psycopg.rows import dict_row

        _pg_pool = ConnectionPool(
            conninfo=_pg_conninfo(settings.DATABASE_URL),
            max_size=10,
            open=True,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        checkpointer = PostgresSaver(_pg_pool)
        checkpointer.setup()  # idempotent: creates checkpoint tables on first run
        logger.info("Postgres checkpointer initialized (durable crash recovery).")
        return checkpointer
    except Exception as e:  # noqa: BLE001 — degrade to the next backend
        logger.warning(f"Postgres checkpointer unavailable ({type(e).__name__}: {str(e)[:100]}).")
        return None


def get_checkpointer():
    """
    Returns a LangGraph-compatible checkpointer (Redis → Postgres → in-memory).

    Usage in graph.py:
        checkpointer = get_checkpointer()
        app = workflow.compile(checkpointer=checkpointer)

    Resuming after a crash:
        config = {"configurable": {"thread_id": str(workflow_id)}}
        app.invoke(None, config=config)   # None → resume from last checkpoint
    """
    # Keep the test suite hermetic — never reach out to Redis/Postgres under pytest.
    if settings.ENVIRONMENT == "testing":
        return _in_memory()

    return _try_redis() or _try_postgres() or _fallback_in_memory()


def _fallback_in_memory():
    logger.warning("No durable checkpointer available; using in-memory (NO crash recovery).")
    return _in_memory()
