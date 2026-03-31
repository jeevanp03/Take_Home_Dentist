"""Tests for Phase 2A — Gemini LLM client and message converter.

Run from apps/backend/:
    .venv/bin/python -m pytest tests/test_llm.py -v          # unit only
    .venv/bin/python -m pytest tests/test_llm.py -m integration -v -s  # live API
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from google.genai import types

from src.agent.llm import build_config, call_gemini, MODEL_NAME
from src.agent.message_converter import (
    build_function_response_message,
    build_function_response_part,
    history_to_contents,
    message_to_content,
    response_to_messages,
)

# Import shared mocks from conftest (single source of truth)
from tests.conftest import (
    MockCandidate,
    MockContent,
    MockFunctionCall,
    MockPart,
    MockResponse,
)


# ---------------------------------------------------------------------------
# Unit tests — message converter with realistic dental conversation flows
# ---------------------------------------------------------------------------

class TestBookingConversationFlow:
    """Simulates a patient booking an appointment — the most common flow."""

    def test_patient_greeting_converts_to_user_role(self):
        msg = {"role": "user", "content": "Hi, I'd like to book a cleaning."}
        result = message_to_content(msg)
        assert result.role == "user"
        assert result.parts[0].text == msg["content"]

    def test_mia_response_converts_to_model_role(self):
        msg = {"role": "assistant", "content": "I'd be happy to help you book a cleaning! Could I get your name, please?"}
        result = message_to_content(msg)
        assert result.role == "model"
        assert result.parts[0].text == msg["content"]

    def test_function_call_preserves_tool_args(self):
        msg = {
            "role": "function_call",
            "name": "lookup_patient",
            "args": {"name": "Sarah Chen", "phone": "555-0142"},
        }
        result = message_to_content(msg)
        assert result.role == "model"
        fc = result.parts[0].function_call
        assert fc.name == "lookup_patient"
        assert fc.args["name"] == "Sarah Chen"
        assert fc.args["phone"] == "555-0142"

    def test_function_response_wraps_patient_record(self):
        patient_data = {
            "patient_id": "abc123",
            "full_name": "Sarah Chen",
            "phone": "555-0142",
            "insurance_name": "Delta Dental",
        }
        msg = {
            "role": "function_response",
            "name": "lookup_patient",
            "response": patient_data,
        }
        result = message_to_content(msg)
        assert result.role == "user"
        fr = result.parts[0].function_response
        assert fr.name == "lookup_patient"

    def test_full_booking_history_merges_correctly(self):
        """A realistic 6-message booking flow — validates merge logic."""
        messages = [
            {"role": "user", "content": "I need a cleaning next Tuesday."},
            {"role": "assistant", "content": "Let me look up available slots for next Tuesday."},
            {"role": "function_call", "name": "get_available_slots", "args": {"date_start": "2026-04-07", "date_end": "2026-04-07"}},
            {"role": "function_response", "name": "get_available_slots", "response": {"slots": [{"id": "s1", "time": "9:00 AM"}, {"id": "s2", "time": "2:00 PM"}]}},
            {"role": "assistant", "content": "I have two openings: 9:00 AM or 2:00 PM. Which works better?"},
            {"role": "user", "content": "2 PM please."},
        ]
        contents = history_to_contents(messages)

        # user, model(text + function_call merged), user(function_response),
        # model(text), user(text)
        assert len(contents) == 5
        assert contents[0].role == "user"
        assert contents[1].role == "model"
        assert len(contents[1].parts) == 2  # text + function_call merged
        assert contents[2].role == "user"   # function_response
        assert contents[3].role == "model"
        assert contents[4].role == "user"


class TestEmergencyConversationFlow:
    """Emergency flow — validates string results and notify_staff tool."""

    def test_string_function_response_wrapped_in_dict(self):
        msg = build_function_response_message(
            "notify_staff",
            "Emergency alert sent to Dr. Smith for patient Sarah Chen.",
        )
        assert msg["role"] == "function_response"
        assert msg["response"]["result"].startswith("Emergency alert")

    def test_dict_function_response_passed_through(self):
        msg = build_function_response_message(
            "notify_staff",
            {"status": "sent", "recipient": "Dr. Smith"},
        )
        assert msg["response"]["status"] == "sent"
        assert msg["response"]["recipient"] == "Dr. Smith"

    def test_function_response_part_with_dict(self):
        part = build_function_response_part(
            "notify_staff",
            {"status": "sent", "recipient": "Dr. Smith"},
        )
        assert part.function_response.name == "notify_staff"

    def test_function_response_part_with_string(self):
        part = build_function_response_part(
            "notify_staff",
            "Staff notified successfully.",
        )
        assert part.function_response.name == "notify_staff"


class TestParallelToolCalls:
    """When Gemini calls multiple tools simultaneously."""

    def test_parallel_calls_merge_into_single_model_turn(self):
        messages = [
            {"role": "user", "content": "I'm Jane Doe, 555-0101. Any slots tomorrow?"},
            {"role": "function_call", "name": "lookup_patient", "args": {"name": "Jane Doe", "phone": "555-0101"}},
            {"role": "function_call", "name": "get_available_slots", "args": {"date_start": "2026-04-01", "date_end": "2026-04-01"}},
            {"role": "function_response", "name": "lookup_patient", "response": {"patient_id": "p1"}},
            {"role": "function_response", "name": "get_available_slots", "response": {"slots": []}},
            {"role": "assistant", "content": "I found your record, Jane. Unfortunately no slots tomorrow."},
        ]
        contents = history_to_contents(messages)
        # user, model(2 fc), user(2 fr), model(text)
        assert len(contents) == 4
        assert len(contents[1].parts) == 2
        assert len(contents[2].parts) == 2

    def test_empty_args_handled(self):
        msg = {"role": "function_call", "name": "get_practice_info", "args": {}}
        result = message_to_content(msg)
        assert result.parts[0].function_call.name == "get_practice_info"


class TestEdgeCases:
    """Edge cases that could break the converter in production."""

    def test_unknown_role_skipped(self):
        assert message_to_content({"role": "system", "content": "ignored"}) is None

    def test_empty_history_returns_empty(self):
        assert history_to_contents([]) == []

    def test_function_response_with_non_dict_response(self):
        msg = {
            "role": "function_response",
            "name": "cancel_appointment",
            "response": "Appointment A1 cancelled successfully.",
        }
        result = message_to_content(msg)
        assert result.parts[0].function_response.name == "cancel_appointment"

    def test_mixed_valid_and_invalid_messages(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "system", "content": "should be skipped"},
            {"role": "assistant", "content": "Hi!"},
        ]
        contents = history_to_contents(messages)
        assert len(contents) == 2

    def test_function_call_missing_name_skipped(self):
        msg = {"role": "function_call", "args": {"date": "2026-04-01"}}
        assert message_to_content(msg) is None

    def test_function_response_missing_name_skipped(self):
        msg = {"role": "function_response", "response": {"data": "test"}}
        assert message_to_content(msg) is None

    def test_user_message_with_empty_content(self):
        msg = {"role": "user", "content": ""}
        result = message_to_content(msg)
        assert result.role == "user"
        assert result.parts[0].text == ""

    def test_message_missing_content_key_uses_empty_string(self):
        msg = {"role": "user"}
        result = message_to_content(msg)
        assert result.parts[0].text == ""


# ---------------------------------------------------------------------------
# Unit tests — response_to_messages (mocked responses, no API calls)
# ---------------------------------------------------------------------------

class TestResponseToMessages:
    """Unit tests for parsing Gemini responses into Redis message dicts."""

    def test_text_response(self):
        response = MockResponse(
            candidates=[MockCandidate(content=MockContent(
                parts=[MockPart(text="Hello! How can I help?")]
            ))]
        )
        msgs = response_to_messages(response)
        assert len(msgs) == 1
        assert msgs[0] == {"role": "assistant", "content": "Hello! How can I help?"}

    def test_function_call_response(self):
        response = MockResponse(
            candidates=[MockCandidate(content=MockContent(
                parts=[MockPart(
                    function_call=MockFunctionCall(
                        name="get_available_slots",
                        args={"date_start": "2026-04-01"},
                    )
                )]
            ))]
        )
        msgs = response_to_messages(response)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "function_call"
        assert msgs[0]["name"] == "get_available_slots"
        assert msgs[0]["args"]["date_start"] == "2026-04-01"

    def test_mixed_text_and_function_call(self):
        """Gemini sometimes says 'Let me check' AND issues a tool call."""
        response = MockResponse(
            candidates=[MockCandidate(content=MockContent(
                parts=[
                    MockPart(text="Let me look that up for you."),
                    MockPart(function_call=MockFunctionCall(
                        name="get_available_slots",
                        args={"date_start": "2026-04-01"},
                    )),
                ]
            ))]
        )
        msgs = response_to_messages(response)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "assistant"
        assert msgs[1]["role"] == "function_call"

    def test_no_candidates_returns_empty(self):
        response = MockResponse(candidates=[])
        msgs = response_to_messages(response)
        assert msgs == []

    def test_blocked_response_with_none_content(self):
        """Safety-blocked response — candidate exists but content is None."""
        response = MockResponse(
            candidates=[MockCandidate(content=None, finish_reason="SAFETY")]
        )
        msgs = response_to_messages(response)
        assert msgs == []

    def test_blocked_response_with_none_parts(self):
        """Candidate has content but parts is None."""
        response = MockResponse(
            candidates=[MockCandidate(content=MockContent(parts=None))]
        )
        msgs = response_to_messages(response)
        assert msgs == []

    def test_function_call_with_empty_args(self):
        response = MockResponse(
            candidates=[MockCandidate(content=MockContent(
                parts=[MockPart(function_call=MockFunctionCall(
                    name="get_practice_info", args={}
                ))]
            ))]
        )
        msgs = response_to_messages(response)
        assert msgs[0]["args"] == {}

    def test_function_call_with_none_args(self):
        response = MockResponse(
            candidates=[MockCandidate(content=MockContent(
                parts=[MockPart(function_call=MockFunctionCall(
                    name="get_practice_info", args=None
                ))]
            ))]
        )
        msgs = response_to_messages(response)
        assert msgs[0]["args"] == {}


# ---------------------------------------------------------------------------
# Unit tests — build_config
# ---------------------------------------------------------------------------

class TestBuildConfig:
    """Verify build_config produces correct defaults."""

    def test_default_config_values(self):
        config = build_config()
        assert config.temperature == 0.4
        assert config.top_p == 0.9
        assert config.max_output_tokens == 1024
        assert len(config.safety_settings) == 4

    def test_config_with_tools(self):
        tool = types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name="test_tool",
                description="A test tool",
            )
        ])
        config = build_config(tools=[tool])
        assert config.tools is not None
        assert len(config.tools) == 1

    def test_config_with_system_instruction(self):
        config = build_config(system_instruction="You are Mia.")
        assert config.system_instruction == "You are Mia."

    def test_config_custom_temperature(self):
        config = build_config(temperature=0.8)
        assert config.temperature == 0.8


# ---------------------------------------------------------------------------
# Integration tests — live Gemini API (run with: pytest -m integration -v -s)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="class")
@pytest.mark.integration
class TestGeminiIntegration:
    """Live API tests.  Run only when quota is available:
        .venv/bin/python -m pytest tests/test_llm.py -m integration -v -s
    """

    async def test_dental_query_not_blocked_by_safety(self):
        """Clinical dental content must not trigger safety filters."""
        config = build_config()
        response = await call_gemini(
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(
                        text="I have bleeding gums and tooth pain after "
                             "an extraction. What should I expect?"
                    )],
                )
            ],
            config=config,
        )
        assert response.candidates, "Dental content was blocked by safety filters!"
        text = response.candidates[0].content.parts[0].text
        assert len(text) > 20
        print(f"\n  [safety] Response ({len(text)} chars): {text[:100]}...")

    async def test_tool_call_and_response_roundtrip(self):
        """User asks for slots → Gemini calls tool → we send result → text reply."""
        tool = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="get_available_slots",
                    description="Get available appointment slots for a date range at the dental office.",
                    parameters=types.Schema(
                        type="OBJECT",
                        properties={
                            "date_start": types.Schema(type="STRING", description="Start date (YYYY-MM-DD)"),
                            "date_end": types.Schema(type="STRING", description="End date (YYYY-MM-DD)"),
                        },
                        required=["date_start", "date_end"],
                    ),
                )
            ]
        )

        config = build_config(
            tools=[tool],
            system_instruction=(
                "You are Mia, a dental office assistant at Bright Smile Dental. "
                "Today is 2026-03-31. Always use tools to check availability. "
                "Never make up appointment times."
            ),
        )

        # Turn 1: user asks
        response = await call_gemini(
            contents=[
                types.Content(role="user", parts=[
                    types.Part.from_text(text="Any openings tomorrow afternoon?")
                ]),
            ],
            config=config,
        )
        msgs = response_to_messages(response)
        fc_msgs = [m for m in msgs if m["role"] == "function_call"]
        assert len(fc_msgs) >= 1, f"Expected function_call, got: {[m['role'] for m in msgs]}"
        print(f"\n  [turn 1] Tool: {fc_msgs[0]['name']}({fc_msgs[0]['args']})")

        # Turn 2: send tool result, get human reply
        fn_part = build_function_response_part(
            "get_available_slots",
            {"slots": [
                {"id": "s1", "date": "2026-04-01", "start_time": "13:00", "end_time": "13:30", "provider": "Dr. Smith"},
                {"id": "s2", "date": "2026-04-01", "start_time": "15:00", "end_time": "15:30", "provider": "Dr. Smith"},
            ]},
        )
        response2 = await call_gemini(
            contents=[
                types.Content(role="user", parts=[types.Part.from_text(text="Any openings tomorrow afternoon?")]),
                types.Content(role="model", parts=list(response.candidates[0].content.parts)),
                types.Content(role="user", parts=[fn_part]),
            ],
            config=config,
        )
        msgs2 = response_to_messages(response2)
        text_msgs = [m for m in msgs2 if m["role"] == "assistant"]
        assert len(text_msgs) > 0, "Expected text after tool result"
        print(f"\n  [turn 2] Mia: {text_msgs[0]['content'][:150]}...")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
