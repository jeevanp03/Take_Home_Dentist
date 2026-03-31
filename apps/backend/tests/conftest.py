"""Shared test fixtures and mock objects for the dental chatbot tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import (
    Appointment,
    AppointmentStatus,
    AppointmentType,
    Base,
    Patient,
    TimeSlot,
)
from src.db.database import get_db
from src.main import app
from src.api.auth import create_access_token


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


# ---------------------------------------------------------------------------
# Shared DB fixtures
# ---------------------------------------------------------------------------

PROVIDER = "Dr. Sarah Smith"
TODAY = date.today()
# Use next Monday to ensure weekday slots
_next_monday = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)
SLOT_DATE = _next_monday


def _create_slots(db, slot_date: date, count: int = 10) -> list[TimeSlot]:
    """Create `count` 30-min slots starting at 8:00 AM on `slot_date`."""
    slots = []
    for i in range(count):
        hour = 8 + (i * 30) // 60
        minute = (i * 30) % 60
        slot = TimeSlot(
            date=slot_date,
            start_time=time(hour, minute),
            end_time=time(hour + (minute + 30) // 60, (minute + 30) % 60),
            provider_name=PROVIDER,
            is_available=True,
        )
        db.add(slot)
        slots.append(slot)
    db.commit()
    for s in slots:
        db.refresh(s)
    return slots


@pytest.fixture
def test_engine():
    """Create an in-memory SQLite engine."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def test_db(test_engine):
    """Create a DB session with seeded data for integration tests."""
    TestSession = sessionmaker(bind=test_engine)
    db = TestSession()
    yield db
    db.close()


@pytest.fixture
def seeded_db(test_db):
    """DB session with seed patients, slots, and an appointment."""
    db = test_db

    # Patients
    p1 = Patient(
        id="patient_sarah",
        full_name="Sarah Johnson",
        phone="5550101234",
        date_of_birth=date(1990, 5, 15),
        insurance_name="Delta Dental",
    )
    p2 = Patient(
        id="patient_mike",
        full_name="Mike Wilson",
        phone="5550102345",
        date_of_birth=date(1985, 3, 20),
        insurance_name=None,
    )
    db.add_all([p1, p2])
    db.commit()

    # Slots — 10 slots on SLOT_DATE, 5 on day after
    slots_day1 = _create_slots(db, SLOT_DATE, 10)
    slots_day2 = _create_slots(db, SLOT_DATE + timedelta(days=1), 5)

    # Book one appointment for Sarah on the first slot
    slots_day1[0].is_available = False
    appt = Appointment(
        id="appt_sarah_1",
        patient_id="patient_sarah",
        slot_id=slots_day1[0].id,
        appointment_type=AppointmentType.cleaning,
        status=AppointmentStatus.scheduled,
    )
    db.add(appt)
    db.commit()

    return db


@pytest.fixture
def client(seeded_db):
    """FastAPI test client with overridden DB dependency."""
    def _override_db():
        yield seeded_db

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_header(client) -> dict:
    """Get a valid auth header from the token endpoint."""
    resp = client.post("/api/auth/token")
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def session_id(client) -> str:
    """Get the session_id from a freshly issued token."""
    resp = client.post("/api/auth/token")
    return resp.json()["session_id"]


def auth_for_session(session_id: str) -> dict:
    """Create an auth header for a specific session_id."""
    token, _ = create_access_token(session_id=session_id)
    return {"Authorization": f"Bearer {token}"}
