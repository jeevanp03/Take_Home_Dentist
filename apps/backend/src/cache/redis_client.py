"""Async Redis client with connection pool and in-memory fallback."""

from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from src.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton pool / client
# ---------------------------------------------------------------------------
_pool: aioredis.ConnectionPool | None = None
_client: aioredis.Redis | None = None

# In-memory fallback store — used when Redis is unreachable.
_fallback_store: dict[str, Any] = {}
_using_fallback: bool = False


def _create_pool() -> aioredis.ConnectionPool:
    """Create a connection pool from the configured REDIS_URL."""
    settings = get_settings()
    return aioredis.ConnectionPool.from_url(
        settings.REDIS_URL,
        max_connections=20,
        decode_responses=True,
    )


async def get_redis() -> aioredis.Redis:
    """Return the singleton async Redis client.

    On first call the connection pool is lazily created.  If Redis is
    unreachable the module falls back to an in-memory dict (logged as a
    warning).  Every subsequent call re-attempts a real connection so the
    system self-heals once Redis comes back.
    """
    global _pool, _client, _using_fallback  # noqa: PLW0603

    # Always try to (re)establish a real connection.
    if _pool is None:
        _pool = _create_pool()

    if _client is None:
        _client = aioredis.Redis(connection_pool=_pool)

    # Quick health-check — ping to make sure Redis is alive.
    try:
        await _client.ping()
        if _using_fallback:
            logger.info("Redis connection restored — switching back from in-memory fallback.")
            _using_fallback = False
        return _client
    except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
        if not _using_fallback:
            logger.warning(
                "Redis unavailable (%s). Using in-memory fallback. "
                "Will retry real connection on next request.",
                exc,
            )
            _using_fallback = True
        # Reset client so next call will retry.
        _client = None
        _pool = None
        # Return a sentinel that callers can detect.
        raise


def is_using_fallback() -> bool:
    """Return *True* when the module is currently operating in fallback mode."""
    return _using_fallback


def get_fallback_store() -> dict[str, Any]:
    """Return the in-memory fallback dict (for use by session.py)."""
    return _fallback_store


async def close_redis() -> None:
    """Cleanly shut down the Redis connection pool."""
    global _pool, _client  # noqa: PLW0603
    if _client is not None:
        await _client.aclose()
        _client = None
    if _pool is not None:
        await _pool.aclose()
        _pool = None
