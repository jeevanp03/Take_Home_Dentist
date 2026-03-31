"""ReAct orchestrator — drives the agent loop for each user turn.

Public entry point::

    async for chunk in run(session_id, user_message, db):
        # chunk is a dict: {"type": "text", "content": "..."} or
        #                   {"type": "end",  "content": ""}  or
        #                   {"type": "error","content": "..."}
        ...

The orchestrator handles:
- Session locking (one agent run per session at a time)
- Input sanitisation
- Message history → Gemini Content conversion
- ReAct loop (observe → think → act, max 5 iterations)
- Repeated-call detection + finish_reason checking
- Intermediate text streaming
- Conversation-end lifecycle (summarise → ChromaDB → SQLite → clear Redis)

PHI NOTE: Conversation summaries stored in ChromaDB and SQLite may contain
patient names, DOB, insurance status, and appointment details. These tables
must be treated as PHI under HIPAA. See ``_end_conversation`` for details.
"""

from __future__ import annotations

import json
import logging
import re
import time as _time
from collections.abc import AsyncGenerator
from typing import Any

from google.genai import types
from sqlalchemy.orm import Session

from src.agent.llm import build_config, call_gemini, MODEL_NAME
from src.agent.message_converter import (
    build_function_response_message,
    build_function_response_part,
    history_to_contents,
    response_to_messages,
)
from src.agent.system_prompt import build_system_prompt
from src.agent.tools import execute_tool, get_tool_declarations
from src.cache.session import (
    acquire_session_lock,
    append_message,
    clear_session,
    get_session,
    release_session_lock,
    update_session,
)
from src.db.repositories import ConversationLogRepository
from src.vector.chroma_client import get_conversations_collection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_TOOL_ITERATIONS = 5
MAX_MESSAGES = 50          # context-window guard (Gemini Flash 1M is generous)
MAX_INPUT_CHARS = 2000
MAX_OUTPUT_TOKENS = 1536   # bumped from 1024 — family/multi-slot responses need room

_FALLBACK_MSG = (
    "I'm having some trouble right now. "
    "You can reach us directly at (555) 123-4567."
)

# Goodbye detection — phrases must appear as near-complete messages.
# Anchored patterns avoid false positives like "take care of my teeth".
_GOODBYE_RE = re.compile(
    r"(?:^|\.\s*)"                 # start of string or after a sentence
    r"(?:"
    r"bye\b|goodbye\b|good\s*bye\b|"
    r"thanks?\s*(?:that'?s?\s*all|so\s*much)\b|"
    r"that'?s?\s*it\b|"
    r"have\s*a\s*good\b|"
    r"take\s*care\s*$|"            # "take care" only at end (not "take care of")
    r"see\s*you\b|"
    r"no\s*thanks\s*$|"            # "no thanks" only at end (not "no thanks, I have more")
    r"(?:i'?m\s*)?all\s*set\b|"
    r"done\s*thanks?\b|"
    r"no\s*more\s*questions?\b"
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Chunk helpers (SSE-friendly dicts)
# ---------------------------------------------------------------------------

def _text_chunk(text: str) -> dict[str, str]:
    return {"type": "text", "content": text}


def _end_chunk() -> dict[str, str]:
    return {"type": "end", "content": ""}


def _error_chunk(msg: str) -> dict[str, str]:
    return {"type": "error", "content": msg}


# ---------------------------------------------------------------------------
# Input sanitisation
# ---------------------------------------------------------------------------

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitise(text: str) -> str:
    """Strip control characters and truncate to MAX_INPUT_CHARS."""
    cleaned = _CONTROL_CHARS.sub("", text).strip()
    if len(cleaned) > MAX_INPUT_CHARS:
        logger.warning("Input truncated from %d to %d chars.", len(cleaned), MAX_INPUT_CHARS)
        cleaned = cleaned[:MAX_INPUT_CHARS]
    return cleaned


# ---------------------------------------------------------------------------
# Context-window trimming
# ---------------------------------------------------------------------------

def _trim_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the most recent messages within the MAX_MESSAGES budget.

    We keep the last N messages (not the first) so the agent always has
    the freshest context.  No mid-conversation summarisation — Gemini
    Flash's 1M context window makes it unnecessary.
    """
    if len(messages) <= MAX_MESSAGES:
        return messages
    logger.info(
        "Trimming history from %d to %d messages.", len(messages), MAX_MESSAGES,
    )
    return messages[-MAX_MESSAGES:]


# ---------------------------------------------------------------------------
# Repeated-call detection
# ---------------------------------------------------------------------------

def _call_signature(name: str, args: dict) -> str:
    """Deterministic string key for a tool call (name + sorted args)."""
    return f"{name}::{json.dumps(args, sort_keys=True, default=str)}"


# ---------------------------------------------------------------------------
# Gemini response helpers
# ---------------------------------------------------------------------------

def _is_blocked_or_truncated(response: types.GenerateContentResponse) -> str | None:
    """Check if a Gemini response was blocked or truncated.

    Returns a reason string if problematic, None if OK.
    """
    if not response.candidates:
        return "no_candidates"
    candidate = response.candidates[0]
    reason = getattr(candidate, "finish_reason", None)
    # finish_reason is an enum or string depending on SDK version
    reason_str = str(reason).upper() if reason else ""
    if "SAFETY" in reason_str:
        return "safety_blocked"
    if "MAX_TOKENS" in reason_str:
        return "max_tokens"
    return None


# ---------------------------------------------------------------------------
# Goodbye detection
# ---------------------------------------------------------------------------

def _is_goodbye(text: str) -> bool:
    """Return True if the user's message matches a goodbye pattern.

    Uses anchored patterns to avoid false positives (e.g. "take care of
    my teeth" does NOT trigger, but "take care" at end of message does).
    """
    # Short messages that are mostly a goodbye phrase
    stripped = text.strip()
    if len(stripped) > 100:
        # Long messages are unlikely to be pure goodbyes
        return False
    return bool(_GOODBYE_RE.search(stripped))


# ---------------------------------------------------------------------------
# Conversation-end lifecycle
# ---------------------------------------------------------------------------

async def _end_conversation(
    session_id: str,
    session: dict[str, Any],
    db: Session,
) -> None:
    """Summarise, persist to ChromaDB + SQLite, and clear Redis.

    Each step is in its own try/except — a failure in one doesn't block
    the rest.

    PHI WARNING: The summary and conversation log may contain patient names,
    DOB, insurance status, and appointment details. The ``conversation_logs``
    table and ChromaDB ``conversations`` collection must be treated as PHI
    under HIPAA. Access should be restricted and data encrypted at rest in
    production deployments.
    """
    messages = session.get("messages", [])
    patient_id = session.get("patient_id")

    # --- Step 1: Structured summarisation via Gemini -----------------------
    summary: str | None = None
    try:
        summary = await _summarise_conversation(messages)
    except Exception:
        logger.exception("Failed to summarise conversation for session %s", session_id)

    # --- Step 2: Store in ChromaDB conversations collection ----------------
    # PHI: We store only the summary (not full transcript) in ChromaDB to
    # minimise PHI exposure. The summary may still contain patient names
    # and appointment details — treat the conversations collection as PHI.
    try:
        collection = get_conversations_collection()
        doc_id = f"conv_{session_id}"
        metadata: dict[str, Any] = {
            "session_id": session_id,
            "timestamp": _time.time(),
        }
        if patient_id:
            metadata["patient_id"] = patient_id

        # Store the summary for embedding (not the full transcript).
        # This reduces PHI footprint while still enabling semantic search
        # over past conversations.
        doc_text = summary if summary else _build_conversation_digest(messages)

        if doc_text:
            collection.upsert(
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[metadata],
            )
            logger.info("Stored conversation summary %s in ChromaDB.", session_id)
    except Exception:
        logger.exception("Failed to store conversation in ChromaDB for session %s", session_id)

    # --- Step 3: Log to SQLite ConversationLog ----------------------------
    # PHI: conversation_logs.messages contains the full transcript as JSON.
    # conversation_logs.summary may contain patient names and insurance status.
    # This table must be encrypted at rest and access-controlled in production.
    try:
        existing = ConversationLogRepository.find_by_session(db, session_id)
        if existing:
            ConversationLogRepository.end_conversation(db, session_id, summary=summary)
        else:
            ConversationLogRepository.create(
                db,
                session_id=session_id,
                messages=json.dumps(messages, default=str),
                patient_id=patient_id,
                summary=summary,
            )
        logger.info("Logged conversation %s to SQLite.", session_id)
    except Exception:
        logger.exception("Failed to log conversation to SQLite for session %s", session_id)

    # --- Step 4: Clear Redis session --------------------------------------
    try:
        await clear_session(session_id)
        logger.info("Cleared Redis session %s.", session_id)
    except Exception:
        logger.exception("Failed to clear Redis session %s", session_id)


def _build_conversation_digest(messages: list[dict[str, Any]]) -> str | None:
    """Build a simple text digest from user/assistant messages (fallback
    when LLM summarisation fails)."""
    text_parts = [
        f"{m['role']}: {m['content']}"
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    if not text_parts:
        return None
    # Keep last 20 text exchanges max
    return "\n".join(text_parts[-20:])


async def _summarise_conversation(messages: list[dict[str, Any]]) -> str | None:
    """Use Gemini to produce a structured conversation summary."""
    # Filter to text messages first, then slice (so we get the last 30
    # conversational exchanges, not the last 30 raw messages which may
    # include verbose function_call/function_response entries).
    text_parts = [
        f"{m['role']}: {m['content']}"
        for m in messages
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    if not text_parts:
        return None

    transcript = "\n".join(text_parts[-30:])

    summarisation_prompt = (
        "Summarise this dental office conversation in a structured format:\n"
        "- Patient name (if mentioned)\n"
        "- What they asked about\n"
        "- What was booked/cancelled/rescheduled (include date, time, type, "
        "and appointment ID if visible)\n"
        "- Unresolved issues or follow-up needed\n"
        "- Insurance status\n\n"
        "Be concise. If a field doesn't apply, omit it.\n\n"
        f"CONVERSATION:\n{transcript}"
    )

    config = build_config(
        temperature=0.2,
        max_output_tokens=400,
        system_instruction="You are a conversation summariser. Output a concise structured summary.",
    )

    try:
        response = await call_gemini(
            contents=[types.Content(
                role="user",
                parts=[types.Part.from_text(text=summarisation_prompt)],
            )],
            config=config,
        )
        if response.candidates and response.candidates[0].content:
            parts = response.candidates[0].content.parts
            if parts and parts[0].text:
                return parts[0].text.strip()
    except Exception:
        logger.exception("Summarisation LLM call failed.")

    return None


# ---------------------------------------------------------------------------
# Main orchestrator loop
# ---------------------------------------------------------------------------

async def run(
    session_id: str,
    user_message: str,
    db: Session,
) -> AsyncGenerator[dict[str, str], None]:
    """Drive one user turn through the ReAct agent loop.

    Yields SSE-friendly dicts: ``{"type": "text"|"end"|"error", "content": ...}``
    """
    # --- Step 0: Acquire session lock -------------------------------------
    lock_token = await acquire_session_lock(session_id)
    if lock_token is None:
        yield _error_chunk("I'm still working on your previous message — one moment!")
        yield _end_chunk()
        return

    try:
        # --- Step 1: Sanitise input ---------------------------------------
        clean_input = _sanitise(user_message)
        if not clean_input:
            yield _text_chunk("I didn't catch that — could you try again?")
            yield _end_chunk()
            return

        # --- Step 2: Load/create session, append user message -------------
        session = await get_session(session_id)
        await append_message(session_id, {"role": "user", "content": clean_input})
        # Refresh session after append
        session = await get_session(session_id)

        # --- Step 3: Build system prompt + convert history ----------------
        system_prompt = build_system_prompt(session)
        trimmed = _trim_history(session.get("messages", []))
        contents = history_to_contents(trimmed)

        # --- Step 4: Call Gemini with tool declarations -------------------
        tool_declarations = get_tool_declarations()
        config = build_config(
            tools=tool_declarations,
            system_instruction=system_prompt,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )

        response = await call_gemini(contents=contents, config=config)

        # Check for safety-blocked or truncated response
        block_reason = _is_blocked_or_truncated(response)
        if block_reason == "safety_blocked":
            logger.warning("Gemini response safety-blocked for session %s", session_id)
            yield _text_chunk(
                "I wasn't able to respond to that. Could you rephrase "
                "your question? I'm here to help with dental appointments "
                "and questions."
            )
            yield _end_chunk()
            return

        response_msgs = response_to_messages(response)

        # --- Step 5: ReAct loop — execute tool calls ----------------------
        iteration = 0
        call_history: dict[str, int] = {}  # signature → count
        final_text_already_yielded = False

        while iteration < MAX_TOOL_ITERATIONS:
            # Separate text and function_call messages
            text_msgs = [m for m in response_msgs if m["role"] == "assistant"]
            fc_msgs = [m for m in response_msgs if m["role"] == "function_call"]

            if not fc_msgs:
                # No tool calls — yield final text and mark as done
                for tm in text_msgs:
                    if tm.get("content"):
                        yield _text_chunk(tm["content"])
                        await append_message(session_id, tm)
                        final_text_already_yielded = True
                break

            # Stream any intermediate text ("Let me look that up...")
            for tm in text_msgs:
                if tm.get("content"):
                    yield _text_chunk(tm["content"])
                    await append_message(session_id, tm)

            # --- Repeated-call detection ----------------------------------
            should_break = False
            for fc in fc_msgs:
                sig = _call_signature(fc["name"], fc.get("args", {}))
                call_history[sig] = call_history.get(sig, 0) + 1
                if call_history[sig] >= 2:
                    logger.warning(
                        "Repeated tool call detected: %s (iteration %d). Breaking.",
                        fc["name"], iteration,
                    )
                    should_break = True
                    break

            if should_break:
                break

            # --- Execute each function call -------------------------------
            function_response_parts: list[types.Part] = []

            for fc in fc_msgs:
                tool_name = fc["name"]
                tool_args = fc.get("args", {})

                # Store function_call in session
                await append_message(session_id, fc)

                logger.info("Executing tool: %s (iteration %d)", tool_name, iteration)
                result = await execute_tool(
                    tool_name, tool_args, db=db, session_id=session_id,
                )

                # Store function_response in session
                fr_msg = build_function_response_message(tool_name, result)
                await append_message(session_id, fr_msg)

                # Build Part for next Gemini call
                function_response_parts.append(
                    build_function_response_part(tool_name, result)
                )

            # --- Call Gemini again with function responses -----------------
            # Append function response parts as a user-role content entry
            contents.append(
                types.Content(role="user", parts=function_response_parts)
            )

            response = await call_gemini(contents=contents, config=config)

            # Check for blocked/truncated mid-loop
            block_reason = _is_blocked_or_truncated(response)
            if block_reason == "safety_blocked":
                logger.warning("Mid-loop safety block at iteration %d", iteration)
                yield _text_chunk(
                    "I wasn't able to process that fully. Could you rephrase "
                    "your request? I'm here to help with dental appointments "
                    "and questions."
                )
                final_text_already_yielded = True
                break

            response_msgs = response_to_messages(response)
            iteration += 1

        # --- Step 5b: Max-iterations guard (2C.4) -------------------------
        if final_text_already_yielded:
            # Text was already streamed in the loop — skip to end checks
            final_text_msgs = []
        else:
            final_text_msgs = [m for m in response_msgs if m["role"] == "assistant"]

        final_fc_msgs = [m for m in response_msgs if m["role"] == "function_call"]

        if final_fc_msgs and not final_text_msgs and not final_text_already_yielded:
            logger.warning(
                "Max iterations (%d) reached with pending tool calls. "
                "Forcing text-only response.",
                MAX_TOOL_ITERATIONS,
            )
            try:
                # Refresh session and rebuild contents
                session = await get_session(session_id)
                trimmed = _trim_history(session.get("messages", []))
                contents = history_to_contents(trimmed)

                no_tool_config = build_config(
                    system_instruction=system_prompt,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    # No tools — forces text output
                )
                response = await call_gemini(contents=contents, config=no_tool_config)
                response_msgs = response_to_messages(response)
                final_text_msgs = [m for m in response_msgs if m["role"] == "assistant"]
            except Exception:
                logger.exception("Forced text-only call also failed.")

        # --- Step 6: Yield final text chunks ------------------------------
        yielded_any_final = False
        for tm in final_text_msgs:
            if tm.get("content"):
                yield _text_chunk(tm["content"])
                await append_message(session_id, tm)
                yielded_any_final = True

        if not yielded_any_final and not final_text_already_yielded:
            # Nothing at all — static fallback
            logger.error("No text output from agent. Using fallback message.")
            yield _text_chunk(_FALLBACK_MSG)
            await append_message(
                session_id, {"role": "assistant", "content": _FALLBACK_MSG},
            )

        # --- Step 7: Check for conversation end ---------------------------
        is_ending = _is_goodbye(clean_input)

        if is_ending:
            logger.info("Goodbye detected for session %s. Ending conversation.", session_id)
            await _end_conversation(session_id, await get_session(session_id), db)

        yield _end_chunk()

    except Exception as exc:
        logger.exception("Orchestrator error for session %s: %s", session_id, exc)
        yield _error_chunk(_FALLBACK_MSG)
        yield _end_chunk()

    finally:
        # --- Step 8: Release session lock ---------------------------------
        await release_session_lock(session_id, lock_token)
