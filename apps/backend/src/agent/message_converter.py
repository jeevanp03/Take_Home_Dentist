"""Convert between Redis session message dicts and Gemini Content/Part objects.

Redis stores messages as simple dicts::

    {"role": "user",      "content": "I need a cleaning"}
    {"role": "assistant",  "content": "I'd be happy to help!"}
    {"role": "function_call",     "name": "get_available_slots", "args": {...}}
    {"role": "function_response", "name": "get_available_slots", "response": {...}}

The ``google-genai`` SDK uses Pydantic-style ``Content`` / ``Part`` objects.

This module bridges the two representations.
"""

from __future__ import annotations

import logging
from typing import Any

from google.genai import types

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_response(raw: Any) -> dict[str, Any]:
    """Ensure a tool response is always a dict (Gemini requires it)."""
    if isinstance(raw, dict):
        return raw
    return {"result": str(raw)}


# ---------------------------------------------------------------------------
# Redis dict → Gemini Content
# ---------------------------------------------------------------------------

def message_to_content(msg: dict[str, Any]) -> types.Content | None:
    """Convert a single Redis message dict to a Gemini ``Content`` object.

    Returns ``None`` for unrecognised or malformed message types.
    """
    role = msg.get("role", "")

    # --- User text ---
    if role == "user":
        content = msg.get("content", "")
        return types.Content(
            role="user",
            parts=[types.Part.from_text(text=content)],
        )

    # --- Assistant text ---
    if role == "assistant":
        content = msg.get("content", "")
        return types.Content(
            role="model",
            parts=[types.Part.from_text(text=content)],
        )

    # --- Function call (model asked to invoke a tool) ---
    if role == "function_call":
        name = msg.get("name")
        if not name:
            logger.warning("function_call message missing 'name' — skipping.")
            return None
        return types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name=name,
                    args=msg.get("args", {}),
                )
            ],
        )

    # --- Function response (tool result sent back) ---
    if role == "function_response":
        name = msg.get("name")
        if not name:
            logger.warning("function_response message missing 'name' — skipping.")
            return None
        return types.Content(
            role="user",
            parts=[
                types.Part.from_function_response(
                    name=name,
                    response=_normalize_response(msg.get("response", {})),
                )
            ],
        )

    logger.warning("Unrecognised message role %r — skipping.", role)
    return None


def history_to_contents(messages: list[dict[str, Any]]) -> list[types.Content]:
    """Convert a full Redis message history to a list of Gemini Content objects.

    Consecutive messages with the same Gemini role are merged into a single
    ``Content`` entry (Gemini requires strict user/model alternation, but
    tool call + tool response sequences can produce consecutive same-role
    entries that need merging).
    """
    raw: list[types.Content] = []
    for msg in messages:
        content = message_to_content(msg)
        if content is not None:
            raw.append(content)

    if not raw:
        return []

    # Merge consecutive entries with the same role.
    merged: list[types.Content] = [raw[0]]
    for entry in raw[1:]:
        if entry.role == merged[-1].role:
            merged[-1].parts.extend(entry.parts)
        else:
            merged.append(entry)

    return merged


# ---------------------------------------------------------------------------
# Gemini response → Redis dicts
# ---------------------------------------------------------------------------

def response_to_messages(
    response: types.GenerateContentResponse,
) -> list[dict[str, Any]]:
    """Extract assistant text and/or function calls from a Gemini response.

    Returns a list of Redis-format message dicts.  A single response can
    contain both text AND one-or-more function calls (parallel tool calling).
    """
    messages: list[dict[str, Any]] = []

    if not response.candidates:
        logger.warning("Gemini response has no candidates.")
        return messages

    candidate = response.candidates[0]

    # Guard against safety-blocked responses where content may be None
    if candidate.content is None or candidate.content.parts is None:
        logger.warning(
            "Gemini candidate has no content (finish_reason=%s).",
            getattr(candidate, "finish_reason", "unknown"),
        )
        return messages

    for part in candidate.content.parts:
        # Text part
        if part.text:
            messages.append({"role": "assistant", "content": part.text})

        # Function call part
        if part.function_call and part.function_call.name:
            fc = part.function_call
            args = dict(fc.args) if fc.args else {}
            messages.append({
                "role": "function_call",
                "name": fc.name,
                "args": args,
            })

    return messages


def build_function_response_message(
    name: str,
    result: dict[str, Any] | str,
) -> dict[str, Any]:
    """Create a Redis-format function_response message dict."""
    return {
        "role": "function_response",
        "name": name,
        "response": _normalize_response(result),
    }


def build_function_response_part(
    name: str,
    result: dict[str, Any] | str,
) -> types.Part:
    """Create a Gemini ``Part`` containing a ``FunctionResponse``.

    This Part gets sent back to Gemini in the next turn so it can see
    the tool result.
    """
    return types.Part.from_function_response(
        name=name,
        response=_normalize_response(result),
    )
