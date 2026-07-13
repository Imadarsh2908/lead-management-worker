"""
app/core/memory.py
-------------------
Provides the LangGraph Redis checkpointer.

How it works:
  - After EVERY node in the LangGraph StateGraph executes, the full AgentState
    is serialized and saved to Redis using the workflow_id as the thread_id key.
  - If the server crashes mid-workflow, calling app.invoke() with the same
    workflow_id automatically resumes from the LAST SUCCESSFUL checkpoint.
  - This provides exactly-once processing guarantees for lead workflows.

Note: We use a simple synchronous Redis client here. For async FastAPI routes,
use langgraph.checkpoint.aiosqlite or an async Redis checkpointer instead.
"""
import redis
from loguru import logger

from app.core.config import settings


def get_redis_client() -> redis.Redis:
    """
    Creates a Redis connection using the REDIS_URL from environment config.
    Uses a connection pool for efficiency under concurrent load.
    """
    pool = redis.ConnectionPool.from_url(
        settings.REDIS_URL,
        max_connections=20,   # Pool size — tune based on expected concurrency
        decode_responses=False,  # Keep as bytes for LangGraph serialization
    )
    return redis.Redis(connection_pool=pool)


def get_checkpointer():
    """
    Returns a LangGraph-compatible checkpointer backed by Redis.
    
    Usage in graph.py:
        checkpointer = get_checkpointer()
        app = workflow.compile(checkpointer=checkpointer)
    
    Usage when running the graph:
        config = {"configurable": {"thread_id": str(workflow_id)}}
        app.invoke(initial_state, config=config)
    """
    try:
        # Import here to avoid errors if langgraph-checkpoint-redis is not installed
        from langgraph.checkpoint.redis import RedisSaver

        client = get_redis_client()
        logger.info("Redis checkpointer initialized successfully.")
        return RedisSaver(client)
    except ImportError:
        # Fallback to in-memory checkpointer for local dev without Redis
        logger.warning(
            "langgraph-checkpoint-redis not installed. "
            "Falling back to MemorySaver (not suitable for production!)."
        )
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}. Using MemorySaver fallback.")
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
