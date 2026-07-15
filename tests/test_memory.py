from unittest.mock import patch, Mock

from app.core.memory import get_redis_client, get_checkpointer, _try_postgres
from app.core.config import settings
from langgraph.checkpoint.memory import InMemorySaver


def test_get_redis_client():
    """Pooled Redis client is built from REDIS_URL."""
    with patch("redis.ConnectionPool.from_url") as mock_pool, patch("redis.Redis") as mock_redis:
        get_redis_client()
        mock_pool.assert_called_once()
        mock_redis.assert_called_once()


def test_checkpointer_testing_env_is_in_memory():
    """Under pytest (ENVIRONMENT=testing) the checkpointer is always hermetic in-memory."""
    assert settings.ENVIRONMENT == "testing"
    assert isinstance(get_checkpointer(), InMemorySaver)


def test_checkpointer_prefers_redis():
    """Outside tests, Redis is preferred and short-circuits before Postgres."""
    redis_cp = Mock(name="redis_saver")
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch("app.core.memory._try_redis", return_value=redis_cp), \
         patch("app.core.memory._try_postgres") as pg:
        assert get_checkpointer() is redis_cp
        pg.assert_not_called()


def test_checkpointer_falls_back_to_postgres_when_no_redis():
    """No Redis → durable Postgres checkpointer."""
    pg_cp = Mock(name="pg_saver")
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch("app.core.memory._try_redis", return_value=None), \
         patch("app.core.memory._try_postgres", return_value=pg_cp):
        assert get_checkpointer() is pg_cp


def test_checkpointer_falls_back_to_memory_when_none_available():
    """Neither Redis nor Postgres → in-memory (graceful, app still starts)."""
    with patch.object(settings, "ENVIRONMENT", "development"), \
         patch("app.core.memory._try_redis", return_value=None), \
         patch("app.core.memory._try_postgres", return_value=None):
        assert isinstance(get_checkpointer(), InMemorySaver)


def test_try_postgres_returns_none_for_non_postgres():
    """_try_postgres is a no-op unless DATABASE_URL is Postgres."""
    with patch.object(settings, "DATABASE_URL", "sqlite:///./test.db"):
        assert _try_postgres() is None
