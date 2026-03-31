"""Shared test fixtures and mock objects for the dental chatbot tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Mock Gemini SDK response objects
# ---------------------------------------------------------------------------
# These mirror the google-genai SDK types just enough for response_to_messages()
# and _is_blocked_or_truncated() to work without importing the real SDK.

@dataclass
class MockFunctionCall:
    name: str
    args: dict = field(default_factory=dict)


@dataclass
class MockPart:
    text: str | None = None
    function_call: MockFunctionCall | None = None


@dataclass
class MockContent:
    parts: list[MockPart] = field(default_factory=list)


@dataclass
class MockCandidate:
    content: MockContent | None = None
    finish_reason: str = "STOP"


@dataclass
class MockResponse:
    candidates: list[MockCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------

def make_text_response(text: str) -> MockResponse:
    return MockResponse(
        candidates=[MockCandidate(
            content=MockContent(parts=[MockPart(text=text)])
        )]
    )


def make_fc_response(tool_name: str, args: dict) -> MockResponse:
    return MockResponse(
        candidates=[MockCandidate(
            content=MockContent(parts=[
                MockPart(function_call=MockFunctionCall(name=tool_name, args=args))
            ])
        )]
    )


def make_text_and_fc_response(text: str, tool_name: str, args: dict) -> MockResponse:
    return MockResponse(
        candidates=[MockCandidate(
            content=MockContent(parts=[
                MockPart(text=text),
                MockPart(function_call=MockFunctionCall(name=tool_name, args=args)),
            ])
        )]
    )


def make_empty_response() -> MockResponse:
    """Response with no candidates (e.g., total safety block)."""
    return MockResponse(candidates=[])


def make_blocked_response() -> MockResponse:
    """Response blocked by safety filters."""
    return MockResponse(
        candidates=[MockCandidate(content=None, finish_reason="SAFETY")]
    )
