"""Cache layer — async Redis client and session state management."""

from src.cache.redis_client import close_redis, get_redis, is_using_fallback
from src.cache.session import (
    acquire_session_lock,
    add_message,
    clear_session,
    get_session,
    release_session_lock,
    update_session,
)

__all__ = [
    "get_redis",
    "close_redis",
    "is_using_fallback",
    "get_session",
    "update_session",
    "add_message",
    "clear_session",
    "acquire_session_lock",
    "release_session_lock",
]
