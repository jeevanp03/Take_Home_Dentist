"""SMS-style message debouncing.

When patients type quickly and send multiple short messages in rapid
succession ("Hi" → "I need" → "a cleaning"), this module holds
incoming messages for a short window and concatenates them before
dispatching a single agent run.

Usage::

    result = await debounce_message(session_id, message)
    if result is None:
        # Message was buffered, will be dispatched with the next one
        return
    # result is the concatenated message — dispatch to orchestrator
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEBOUNCE_SECONDS = 2.5  # how long to wait before dispatching

# ---------------------------------------------------------------------------
# In-memory buffer: session_id → {messages: list[str], event: asyncio.Event}
# ---------------------------------------------------------------------------
_buffers: dict[str, dict[str, Any]] = {}
_lock = asyncio.Lock()


async def debounce_message(session_id: str, message: str) -> str | None:
    """Buffer a message for ``session_id``.

    Returns
    -------
    str | None
        The concatenated message if the debounce window has elapsed
        (this caller should dispatch it).  ``None`` if the message was
        buffered and a prior caller is already waiting.
    """
    async with _lock:
        if session_id in _buffers:
            # Append to existing buffer — the first caller is still waiting
            _buffers[session_id]["messages"].append(message)
            return None

        # First message — create buffer and become the dispatcher
        _buffers[session_id] = {"messages": [message]}

    # Wait for the debounce window (outside the lock)
    await asyncio.sleep(DEBOUNCE_SECONDS)

    # Collect all buffered messages and clear
    async with _lock:
        buf = _buffers.pop(session_id, None)

    if buf is None:
        return message  # shouldn't happen, but safe fallback

    concatenated = " ".join(buf["messages"])
    if len(buf["messages"]) > 1:
        logger.info(
            "Debounced %d messages for session %s into one.",
            len(buf["messages"]), session_id,
        )
    return concatenated
