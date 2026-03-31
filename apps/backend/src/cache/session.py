"""Session state CRUD backed by Redis with in-memory fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from src.cache.redis_client import get_fallback_store, get_redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SESSION_TTL: int = 1800  # 30 minutes in seconds
SESSION_PREFIX: str = "session:"
LOCK_PREFIX: str = "lock:session:"
LOCK_TIMEOUT: int = 120  # seconds — must exceed max agent turn time (5 iters × ~20s)
TTL_WARNING_THRESHOLD: int = 300  # 5 minutes — flag when TTL drops below this

# ---------------------------------------------------------------------------
# Default session shape
# ---------------------------------------------------------------------------

def _default_session() -> dict[str, Any]:
    """Return a blank session dict."""
    now = time.time()
    return {
        "patient_id": None,
        "messages": [],
        "collected": {},
        "intent": None,
        "booking_state": None,
        "created_at": now,
        "updated_at": now,
    }


def _session_key(session_id: str) -> str:
    return f"{SESSION_PREFIX}{session_id}"


def _lock_key(session_id: str) -> str:
    return f"{LOCK_PREFIX}{session_id}"


# ---------------------------------------------------------------------------
# Helpers for Redis vs fallback
# ---------------------------------------------------------------------------

async def _redis_or_none():
    """Try to get a Redis client; return None on connection failure."""
    try:
        return await get_redis()
    except (RedisConnectionError, RedisTimeoutError, OSError):
        return None


# ---------------------------------------------------------------------------
# In-memory fallback helpers
# ---------------------------------------------------------------------------

_fallback_ttls: dict[str, float] = {}  # key → expiry timestamp
_fallback_lock = asyncio.Lock()
_last_cleanup: float = 0.0
_CLEANUP_INTERVAL: float = 60.0  # seconds between cleanup sweeps


def _fb_is_expired(key: str) -> bool:
    """Check if a fallback key has expired."""
    expiry = _fallback_ttls.get(key)
    if expiry is None:
        return False
    return time.time() > expiry


def _fb_set_ttl(key: str, ttl: int = SESSION_TTL) -> None:
    _fallback_ttls[key] = time.time() + ttl


def _fb_delete(key: str) -> None:
    store = get_fallback_store()
    store.pop(key, None)
    _fallback_ttls.pop(key, None)


def _fb_get(key: str) -> dict[str, Any] | None:
    if _fb_is_expired(key):
        _fb_delete(key)
        return None
    store = get_fallback_store()
    raw = store.get(key)
    if raw is None:
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


def _fb_set(key: str, data: dict[str, Any], ttl: int = SESSION_TTL) -> None:
    store = get_fallback_store()
    store[key] = json.dumps(data, default=str)
    _fb_set_ttl(key, ttl)


def _cleanup_expired_fallback() -> None:
    """Remove expired entries from fallback store. Throttled to run at most
    once every ``_CLEANUP_INTERVAL`` seconds."""
    global _last_cleanup  # noqa: PLW0603
    now = time.time()
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    store = get_fallback_store()
    expired_keys = [k for k, expiry in _fallback_ttls.items() if now > expiry]
    for k in expired_keys:
        store.pop(k, None)
        _fallback_ttls.pop(k, None)
    if expired_keys:
        logger.debug("Fallback cleanup: removed %d expired entries.", len(expired_keys))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_session(session_id: str) -> dict[str, Any]:
    """Retrieve session data. Creates a fresh session if none exists.

    Returns the session dict and includes a ``ttl_warning`` boolean flag
    that is *True* when the remaining TTL is below 5 minutes.
    """
    _cleanup_expired_fallback()
    key = _session_key(session_id)
    r = await _redis_or_none()

    if r is not None:
        raw = await r.get(key)
        if raw is not None:
            session = json.loads(raw)
            ttl = await r.ttl(key)
            session["ttl_warning"] = 0 < ttl < TTL_WARNING_THRESHOLD
            return session
        # No existing session — create one.
        session = _default_session()
        await r.set(key, json.dumps(session, default=str), ex=SESSION_TTL)
        session["ttl_warning"] = False
        return session

    # Fallback path
    data = _fb_get(key)
    if data is not None:
        expiry = _fallback_ttls.get(key, 0)
        remaining = expiry - time.time()
        data["ttl_warning"] = 0 < remaining < TTL_WARNING_THRESHOLD
        return data

    session = _default_session()
    _fb_set(key, session)
    session["ttl_warning"] = False
    return session


async def update_session(session_id: str, **fields: Any) -> dict[str, Any]:
    """Update specific fields on a session and refresh the TTL.

    Returns the updated session dict.
    """
    session = await get_session(session_id)
    session.pop("ttl_warning", None)

    for field, value in fields.items():
        session[field] = value
    session["updated_at"] = time.time()

    key = _session_key(session_id)
    r = await _redis_or_none()

    if r is not None:
        await r.set(key, json.dumps(session, default=str), ex=SESSION_TTL)
    else:
        _fb_set(key, session)

    session["ttl_warning"] = False
    return session


async def add_message(session_id: str, role: str, content: str) -> None:
    """Append a message to the session's messages list and refresh TTL."""
    await append_message(session_id, {"role": role, "content": content})


async def append_message(session_id: str, msg: dict[str, Any]) -> None:
    """Append an arbitrary message dict to the session's messages list.

    Unlike :func:`add_message` (which only stores ``{role, content}``), this
    accepts any dict shape — including ``function_call`` and
    ``function_response`` messages needed by the orchestrator.
    """
    session = await get_session(session_id)
    session.pop("ttl_warning", None)

    session["messages"].append(msg)
    session["updated_at"] = time.time()

    key = _session_key(session_id)
    r = await _redis_or_none()

    if r is not None:
        await r.set(key, json.dumps(session, default=str), ex=SESSION_TTL)
    else:
        _fb_set(key, session)


async def clear_session(session_id: str) -> None:
    """Delete a session entirely."""
    key = _session_key(session_id)
    r = await _redis_or_none()

    if r is not None:
        await r.delete(key)
    else:
        _fb_delete(key)

    logger.info("Session %s cleared.", session_id)


# ---------------------------------------------------------------------------
# Session locking (prevents concurrent agent runs for the same session)
# ---------------------------------------------------------------------------

async def acquire_session_lock(session_id: str) -> str | None:
    """Try to acquire an exclusive lock for the session.

    Uses Redis ``SET key value NX EX`` for atomic lock acquisition.
    Returns a unique lock token (str) if acquired, or *None* if already held.
    Lock auto-expires after ``LOCK_TIMEOUT`` seconds.
    """
    key = _lock_key(session_id)
    token = str(uuid.uuid4())
    r = await _redis_or_none()

    if r is not None:
        # SET NX EX — atomic "set if not exists" with expiry
        acquired = await r.set(key, token, nx=True, ex=LOCK_TIMEOUT)
        return token if acquired else None

    # Fallback: use the in-memory store with asyncio.Lock for atomicity
    async with _fallback_lock:
        store = get_fallback_store()
        expiry = _fallback_ttls.get(key)
        if expiry is not None and time.time() < expiry:
            # Lock still held
            return None
        store[key] = token
        _fallback_ttls[key] = time.time() + LOCK_TIMEOUT
        return token


# Lua script for atomic lock release — only deletes if the token matches.
# Prevents TOCTOU race between GET and DELETE.
_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


async def release_session_lock(session_id: str, token: str) -> None:
    """Release the exclusive session lock only if *token* matches.

    Uses a Lua script for atomic check-and-delete on Redis to prevent
    TOCTOU race conditions.
    """
    key = _lock_key(session_id)
    r = await _redis_or_none()

    if r is not None:
        await r.eval(_RELEASE_LOCK_SCRIPT, 1, key, token)
    else:
        async with _fallback_lock:
            store = get_fallback_store()
            if store.get(key) == token:
                _fb_delete(key)
