"""Tests for the ReAct orchestrator and system prompt builder.

These are pure unit tests — no Gemini API calls. The LLM client is mocked
so we can test the orchestrator's control flow (sanitisation, history
trimming, repeated-call detection, conversation-end lifecycle, etc.)
without burning API quota.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.system_prompt import build_system_prompt, _build_patient_context
from src.agent.orchestrator import (
    _sanitise,
    _trim_history,
    _call_signature,
    _is_goodbye,
    _is_blocked_or_truncated,
    _text_chunk,
    _end_chunk,
    _error_chunk,
    MAX_MESSAGES,
    MAX_TOOL_ITERATIONS,
    run,
)

# Import shared mocks from conftest
from tests.conftest import (
    MockCandidate,
    MockContent,
    MockPart,
    MockResponse,
    make_text_response,
    make_fc_response,
    make_text_and_fc_response,
    make_empty_response,
    make_blocked_response,
)

# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_contains_mia_persona(self):
        prompt = build_system_prompt()
        assert "Mia" in prompt
        assert "Bright Smile Dental" in prompt

    def test_contains_dynamic_date(self):
        prompt = build_system_prompt()
        assert "Today is" in prompt

    def test_contains_few_shot_examples(self):
        prompt = build_system_prompt()
        assert "EXAMPLE 1" in prompt
        assert "EXAMPLE 2" in prompt
        assert "EXAMPLE 3" in prompt
        assert "EXAMPLE 4" in prompt
        assert "EXAMPLE 5" in prompt

    def test_contains_critical_rules(self):
        prompt = build_system_prompt()
        assert "NEVER fabricate" in prompt
        assert "911" in prompt
        assert "ANTI-HALLUCINATION" in prompt
        assert "ONE QUESTION PER TURN" in prompt
        assert "BOOKING RESUME" in prompt
        assert "DENTAL ANXIETY" in prompt

    def test_contains_emergency_escalation(self):
        prompt = build_system_prompt()
        assert "Ludwig's angina" in prompt
        assert "KNOCKED-OUT" in prompt
        assert "30-60 minutes" in prompt

    def test_contains_anti_injection_hardening(self):
        prompt = build_system_prompt()
        assert "SECURITY & SCOPE" in prompt
        assert "developer mode" in prompt
        assert "override" in prompt

    def test_contains_empty_slot_instruction(self):
        prompt = build_system_prompt()
        assert "empty list" in prompt or "no results" in prompt

    def test_returning_patient_no_patient_id_in_prompt(self):
        """patient_id should NOT appear in the system prompt (PHI risk)."""
        session = {
            "intent": "returning",
            "patient_id": "abc123",
            "patient_name": "Sarah",
            "patient_context": {},
        }
        prompt = build_system_prompt(session)
        assert "abc123" not in prompt
        assert "Sarah" in prompt

    def test_returning_patient_context(self):
        session = {
            "intent": "returning",
            "patient_id": "abc123",
            "patient_name": "Sarah",
            "patient_context": {
                "appointments": ["Cleaning on April 7 at 9:00 AM"],
                "history_summary": "Last visited for a checkup.",
            },
        }
        prompt = build_system_prompt(session)
        assert "RETURNING PATIENT" in prompt
        assert "Sarah" in prompt
        assert "Cleaning on April 7" in prompt

    def test_new_patient_context(self):
        session = {
            "intent": "new",
            "patient_name": "John",
            "patient_id": "def456",
        }
        prompt = build_system_prompt(session)
        assert "NEW PATIENT" in prompt
        assert "John" in prompt
        assert "date of birth" in prompt

    def test_question_only_context(self):
        session = {"intent": "question"}
        prompt = build_system_prompt(session)
        assert "QUESTION ONLY" in prompt

    def test_no_session_defaults_to_question(self):
        prompt = build_system_prompt(None)
        assert "PATIENT CONTEXT" not in prompt

    def test_collected_name_fallback(self):
        """If patient_name is absent, falls back to collected.name."""
        session = {"intent": "new", "collected": {"name": "Alex"}}
        prompt = build_system_prompt(session)
        assert "Alex" in prompt

    def test_mode_key_fallback(self):
        """If 'intent' is absent, falls back to 'mode' key."""
        session = {"mode": "returning", "patient_id": "x", "patient_name": "Pat"}
        prompt = build_system_prompt(session)
        assert "RETURNING PATIENT" in prompt


# ---------------------------------------------------------------------------
# Sanitisation tests
# ---------------------------------------------------------------------------


class TestSanitise:
    def test_strips_control_chars(self):
        assert _sanitise("hello\x00world") == "helloworld"

    def test_preserves_normal_text(self):
        assert _sanitise("I need a cleaning") == "I need a cleaning"

    def test_truncates_long_input(self):
        long = "a" * 3000
        result = _sanitise(long)
        assert len(result) == 2000

    def test_strips_whitespace(self):
        assert _sanitise("  hello  ") == "hello"

    def test_empty_after_cleaning(self):
        assert _sanitise("\x00\x01\x02") == ""


# ---------------------------------------------------------------------------
# History trimming tests
# ---------------------------------------------------------------------------


class TestTrimHistory:
    def test_no_trim_when_under_limit(self):
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        assert _trim_history(msgs) == msgs

    def test_trims_to_last_n(self):
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(60)]
        trimmed = _trim_history(msgs)
        assert len(trimmed) == MAX_MESSAGES
        assert trimmed[0]["content"] == f"msg{60 - MAX_MESSAGES}"
        assert trimmed[-1]["content"] == "msg59"


# ---------------------------------------------------------------------------
# Repeated call detection tests
# ---------------------------------------------------------------------------


class TestCallSignature:
    def test_same_args_same_signature(self):
        s1 = _call_signature("get_available_slots", {"date_start": "2026-04-01"})
        s2 = _call_signature("get_available_slots", {"date_start": "2026-04-01"})
        assert s1 == s2

    def test_different_args_different_signature(self):
        s1 = _call_signature("get_available_slots", {"date_start": "2026-04-01"})
        s2 = _call_signature("get_available_slots", {"date_start": "2026-04-02"})
        assert s1 != s2

    def test_different_tools_different_signature(self):
        s1 = _call_signature("lookup_patient", {"name": "Sarah"})
        s2 = _call_signature("create_patient", {"name": "Sarah"})
        assert s1 != s2


# ---------------------------------------------------------------------------
# Blocked/truncated response detection
# ---------------------------------------------------------------------------


class TestBlockedDetection:
    def test_normal_response_returns_none(self):
        resp = make_text_response("Hello")
        assert _is_blocked_or_truncated(resp) is None

    def test_empty_candidates_detected(self):
        resp = make_empty_response()
        assert _is_blocked_or_truncated(resp) == "no_candidates"

    def test_safety_blocked_detected(self):
        resp = make_blocked_response()
        assert _is_blocked_or_truncated(resp) == "safety_blocked"

    def test_max_tokens_detected(self):
        resp = MockResponse(candidates=[
            MockCandidate(content=MockContent(parts=[MockPart(text="partial")]),
                          finish_reason="MAX_TOKENS")
        ])
        assert _is_blocked_or_truncated(resp) == "max_tokens"


# ---------------------------------------------------------------------------
# Goodbye detection tests
# ---------------------------------------------------------------------------


class TestGoodbyeDetection:
    @pytest.mark.parametrize("text", [
        "bye",
        "goodbye",
        "thanks that's all",
        "that's it",
        "take care",
        "I'm all set",
        "no thanks",
        "have a good day",
        "done thanks",
        "no more questions",
        "see you later",
    ])
    def test_detects_goodbyes(self, text):
        assert _is_goodbye(text) is True

    @pytest.mark.parametrize("text", [
        "I need a cleaning",
        "what are your hours?",
        "can I reschedule?",
        "hello",
        "I want to take care of my cavities",     # "take care of" should NOT trigger
        "no thanks, I actually have more questions",  # long msg with continuation
    ])
    def test_non_goodbyes(self, text):
        assert _is_goodbye(text) is False

    def test_long_message_not_goodbye(self):
        """Messages over 100 chars are never treated as goodbye."""
        long = "thanks that's all " + "x" * 100
        assert _is_goodbye(long) is False


# ---------------------------------------------------------------------------
# Chunk helpers tests
# ---------------------------------------------------------------------------


class TestChunks:
    def test_text_chunk(self):
        assert _text_chunk("Hello!") == {"type": "text", "content": "Hello!"}

    def test_end_chunk(self):
        assert _end_chunk() == {"type": "end", "content": ""}

    def test_error_chunk(self):
        assert _error_chunk("broke") == {"type": "error", "content": "broke"}


# ---------------------------------------------------------------------------
# Orchestrator integration tests (mocked LLM + session)
# ---------------------------------------------------------------------------

class TestOrchestratorRun:
    """Test the full orchestrator run() with mocked LLM calls."""

    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    @pytest.fixture
    def mock_session(self):
        """Patch session functions to use in-memory state."""
        session_store: dict[str, dict] = {}

        async def fake_get(sid):
            if sid not in session_store:
                session_store[sid] = {
                    "patient_id": None,
                    "messages": [],
                    "collected": {},
                    "intent": None,
                    "booking_state": None,
                }
            return dict(session_store[sid])

        async def fake_append(sid, msg):
            if sid not in session_store:
                session_store[sid] = {"messages": []}
            session_store[sid].setdefault("messages", []).append(msg)

        async def fake_lock(sid):
            return "test-token"

        async def fake_release(sid, token):
            pass

        async def fake_clear(sid):
            session_store.pop(sid, None)

        async def fake_update(sid, **fields):
            if sid not in session_store:
                session_store[sid] = {"messages": []}
            session_store[sid].update(fields)
            return session_store[sid]

        return {
            "get": fake_get,
            "append": fake_append,
            "lock": fake_lock,
            "release": fake_release,
            "clear": fake_clear,
            "update": fake_update,
            "store": session_store,
        }

    def _patches(self, mock_session):
        """Return a context manager that patches all session + orchestrator deps."""
        from contextlib import contextmanager

        @contextmanager
        def ctx():
            with patch("src.agent.orchestrator.acquire_session_lock", mock_session["lock"]), \
                 patch("src.agent.orchestrator.release_session_lock", mock_session["release"]), \
                 patch("src.agent.orchestrator.get_session", mock_session["get"]), \
                 patch("src.agent.orchestrator.append_message", mock_session["append"]), \
                 patch("src.agent.orchestrator.clear_session", mock_session["clear"]), \
                 patch("src.agent.orchestrator.update_session", mock_session["update"]):
                yield
        return ctx()

    async def _collect_chunks(self, gen) -> list[dict]:
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
        return chunks

    @pytest.mark.asyncio
    async def test_simple_text_response(self, mock_db, mock_session):
        """LLM returns plain text — no tools called."""
        with self._patches(mock_session), \
             patch("src.agent.orchestrator.call_gemini") as mock_llm:

            mock_llm.return_value = make_text_response(
                "Hi! I'm Mia from Bright Smile Dental. How can I help you today?"
            )

            chunks = await self._collect_chunks(run("sess1", "hello", mock_db))

            text_chunks = [c for c in chunks if c["type"] == "text"]
            end_chunks = [c for c in chunks if c["type"] == "end"]

            assert len(text_chunks) == 1
            assert "Mia" in text_chunks[0]["content"]
            assert len(end_chunks) == 1
            mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_call_then_text(self, mock_db, mock_session):
        """LLM calls a tool, gets result, then produces text."""
        with self._patches(mock_session), \
             patch("src.agent.orchestrator.call_gemini") as mock_llm, \
             patch("src.agent.orchestrator.execute_tool") as mock_tool:

            mock_llm.side_effect = [
                make_fc_response("get_practice_info", {}),
                make_text_response("Our office is open Mon-Sat 8AM-6PM!"),
            ]
            mock_tool.return_value = {"hours": "Mon-Sat 8AM-6PM"}

            chunks = await self._collect_chunks(run("sess2", "what are your hours?", mock_db))

            text_chunks = [c for c in chunks if c["type"] == "text"]
            assert any("8AM-6PM" in c["content"] for c in text_chunks)
            assert mock_llm.call_count == 2
            mock_tool.assert_called_once_with(
                "get_practice_info", {}, db=mock_db, session_id="sess2",
            )

    @pytest.mark.asyncio
    async def test_empty_input_rejected(self, mock_db, mock_session):
        """Empty/whitespace-only input gets a polite rejection."""
        with self._patches(mock_session):
            chunks = await self._collect_chunks(run("sess3", "   ", mock_db))

            text_chunks = [c for c in chunks if c["type"] == "text"]
            assert len(text_chunks) == 1
            assert "didn't catch" in text_chunks[0]["content"]

    @pytest.mark.asyncio
    async def test_lock_contention(self, mock_db, mock_session):
        """When lock can't be acquired, user gets a 'still working' message."""
        async def lock_fail(sid):
            return None

        with patch("src.agent.orchestrator.acquire_session_lock", lock_fail), \
             patch("src.agent.orchestrator.release_session_lock", mock_session["release"]):

            chunks = await self._collect_chunks(run("sess4", "hello", mock_db))

            error_chunks = [c for c in chunks if c["type"] == "error"]
            assert len(error_chunks) == 1
            assert "still working" in error_chunks[0]["content"]

    @pytest.mark.asyncio
    async def test_intermediate_text_streamed(self, mock_db, mock_session):
        """Text before a function call is streamed immediately."""
        with self._patches(mock_session), \
             patch("src.agent.orchestrator.call_gemini") as mock_llm, \
             patch("src.agent.orchestrator.execute_tool") as mock_tool:

            mock_llm.side_effect = [
                make_text_and_fc_response(
                    "Let me check that for you!",
                    "search_knowledge_base",
                    {"query": "whitening safety"},
                ),
                make_text_response("According to NIH guidelines, whitening is safe."),
            ]
            mock_tool.return_value = {"chunks": [{"text": "Whitening is safe"}]}

            chunks = await self._collect_chunks(run("sess5", "is whitening safe?", mock_db))

            text_chunks = [c for c in chunks if c["type"] == "text"]
            assert len(text_chunks) >= 2
            assert text_chunks[0]["content"] == "Let me check that for you!"

    @pytest.mark.asyncio
    async def test_goodbye_triggers_end_lifecycle(self, mock_db, mock_session):
        """Goodbye message triggers conversation end lifecycle."""
        with self._patches(mock_session), \
             patch("src.agent.orchestrator.call_gemini") as mock_llm, \
             patch("src.agent.orchestrator._end_conversation") as mock_end:

            mock_llm.return_value = make_text_response(
                "Take care! Don't hesitate to reach out if you need anything."
            )

            chunks = await self._collect_chunks(run("sess6", "goodbye!", mock_db))

            mock_end.assert_called_once()

    @pytest.mark.asyncio
    async def test_repeated_call_breaks_loop(self, mock_db, mock_session):
        """Repeated identical tool calls break the loop after first execution."""
        with self._patches(mock_session), \
             patch("src.agent.orchestrator.call_gemini") as mock_llm, \
             patch("src.agent.orchestrator.execute_tool") as mock_tool:

            same_fc = make_fc_response(
                "get_available_slots",
                {"date_start": "2026-04-01", "date_end": "2026-04-05"},
            )
            mock_llm.side_effect = [same_fc, same_fc, same_fc]
            mock_tool.return_value = {"slots": [], "total_available": 0}

            chunks = await self._collect_chunks(run("sess7", "any slots?", mock_db))

            # First call executes, second detects repeat and breaks
            assert mock_tool.call_count == 1

    @pytest.mark.asyncio
    async def test_safety_blocked_response(self, mock_db, mock_session):
        """Safety-blocked Gemini response yields a user-friendly message."""
        with self._patches(mock_session), \
             patch("src.agent.orchestrator.call_gemini") as mock_llm:

            mock_llm.return_value = make_blocked_response()

            chunks = await self._collect_chunks(run("sess8", "something problematic", mock_db))

            text_chunks = [c for c in chunks if c["type"] == "text"]
            assert len(text_chunks) == 1
            assert "rephrase" in text_chunks[0]["content"]

    @pytest.mark.asyncio
    async def test_fallback_on_empty_response(self, mock_db, mock_session):
        """Empty LLM response (no text, no tools) yields static fallback."""
        with self._patches(mock_session), \
             patch("src.agent.orchestrator.call_gemini") as mock_llm:

            # Response with candidate but empty parts
            mock_llm.return_value = MockResponse(
                candidates=[MockCandidate(content=MockContent(parts=[]))]
            )

            chunks = await self._collect_chunks(run("sess9", "hello", mock_db))

            text_chunks = [c for c in chunks if c["type"] == "text"]
            assert len(text_chunks) == 1
            assert "(555) 123-4567" in text_chunks[0]["content"]

    @pytest.mark.asyncio
    async def test_outer_exception_yields_fallback(self, mock_db, mock_session):
        """Unhandled exception in run() yields error fallback and releases lock."""
        released = {"called": False}
        orig_release = mock_session["release"]

        async def track_release(sid, token):
            released["called"] = True
            await orig_release(sid, token)

        with patch("src.agent.orchestrator.acquire_session_lock", mock_session["lock"]), \
             patch("src.agent.orchestrator.release_session_lock", track_release), \
             patch("src.agent.orchestrator.get_session", side_effect=RuntimeError("boom")), \
             patch("src.agent.orchestrator.append_message", mock_session["append"]):

            chunks = await self._collect_chunks(run("sess10", "hello", mock_db))

            error_chunks = [c for c in chunks if c["type"] == "error"]
            assert len(error_chunks) == 1
            assert "(555) 123-4567" in error_chunks[0]["content"]
            # Lock must be released even on exception
            assert released["called"] is True

    @pytest.mark.asyncio
    async def test_max_iterations_forces_text_only_call(self, mock_db, mock_session):
        """After MAX_TOOL_ITERATIONS, orchestrator calls Gemini without tools."""
        with self._patches(mock_session), \
             patch("src.agent.orchestrator.call_gemini") as mock_llm, \
             patch("src.agent.orchestrator.execute_tool") as mock_tool:

            # 5 different tool calls (different args so repeat detection doesn't fire)
            fc_responses = [
                make_fc_response("get_available_slots", {"date_start": f"2026-04-0{i+1}", "date_end": f"2026-04-0{i+2}"})
                for i in range(MAX_TOOL_ITERATIONS)
            ]
            # After all iterations, forced text-only call returns text
            forced_text = make_text_response("I apologize for the delay. How else can I help?")

            mock_llm.side_effect = fc_responses + [fc_responses[-1], forced_text]
            mock_tool.return_value = {"slots": [], "total_available": 0}

            chunks = await self._collect_chunks(run("sessMax", "find me any slot", mock_db))

            text_chunks = [c for c in chunks if c["type"] == "text"]
            assert any("help" in c["content"].lower() or "apologize" in c["content"].lower()
                        for c in text_chunks) or len(text_chunks) > 0
