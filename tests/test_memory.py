from unittest.mock import patch, Mock
from app.core.memory import get_redis_client, get_checkpointer
from langgraph.checkpoint.memory import MemorySaver


def test_get_redis_client():
    with patch("redis.ConnectionPool.from_url") as mock_pool, patch("redis.Redis") as mock_redis:
        get_redis_client()
        mock_pool.assert_called_once()
        mock_redis.assert_called_once()


def test_get_checkpointer_success():
    # Force successful instantiation of RedisSaver using sys.modules patching
    mock_saver_class = Mock()
    mock_saver_instance = Mock()
    mock_saver_class.return_value = mock_saver_instance
    
    with patch("redis.ConnectionPool.from_url"), patch("redis.Redis"), patch.dict("sys.modules", {"langgraph.checkpoint.redis": Mock(RedisSaver=mock_saver_class)}):
        res = get_checkpointer()
        assert res == mock_saver_instance


def test_get_checkpointer_import_error():
    # Force ImportError on langgraph.checkpoint.redis safely
    with patch.dict("sys.modules", {"langgraph.checkpoint.redis": None}):
        res = get_checkpointer()
        assert isinstance(res, MemorySaver)


def test_get_checkpointer_connection_error():
    # Force Redis connection error
    with patch("redis.ConnectionPool.from_url", side_effect=Exception("Connection failed")), patch.dict("sys.modules", {"langgraph.checkpoint.redis": Mock(RedisSaver=Mock())}):
        res = get_checkpointer()
        assert isinstance(res, MemorySaver)
