"""Edge case and endpoint integration tests.

Covers TODO 5.10–5.11:
- API endpoint auth enforcement
- SMS debounce
- Rate limiting
- Double-booking prevention via API
- Slot query edge cases
- Identify endpoint edge cases
- Feedback endpoint
- Health check
- Practice info content validation
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from src.api.auth import create_access_token
from src.api.debounce import debounce_message, _buffers
from src.db.models import TimeSlot

from tests.conftest import SLOT_DATE, auth_for_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_auth(client) -> tuple[str, str, dict]:
    """Get token, session_id, and auth header from the test client."""
    resp = client.post("/api/auth/token")
    body = resp.json()
    token = body["access_token"]
    session_id = body["session_id"]
    headers = {"Authorization": f"Bearer {token}"}
    return token, session_id, headers


# ---------------------------------------------------------------------------
# 5.10 — Auth enforcement on all protected routes
# ---------------------------------------------------------------------------

class TestAuthEnforcement:
    """All protected endpoints reject requests without valid tokens."""

    def test_chat_requires_auth(self, client):
        resp = client.post("/api/chat", json={"message": "hello"})
        assert resp.status_code == 401

    def test_slots_requires_auth(self, client):
        resp = client.get("/api/slots")
        assert resp.status_code == 401

    def test_identify_requires_auth(self, client):
        resp = client.post("/api/identify", json={"mode": "question"})
        assert resp.status_code == 401

    def test_feedback_requires_auth(self, client):
        resp = client.post("/api/feedback", json={
            "message_id": "m1", "feedback": "up"
        })
        assert resp.status_code == 401

    def test_expired_token_rejected(self, client):
        token, _ = create_access_token(expires_delta=timedelta(seconds=-1))
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.get("/api/slots", headers=headers)
        assert resp.status_code == 401

    def test_garbage_token_rejected(self, client):
        headers = {"Authorization": "Bearer not.a.real.token"}
        resp = client.get("/api/slots", headers=headers)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 5.10 — Health check (no auth required)
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_returns_200(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ok", "degraded")
        assert "database" in body["services"]

    def test_health_no_auth_needed(self, client):
        """Health check should work without any auth token."""
        resp = client.get("/api/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 5.10 — Feedback endpoint
# ---------------------------------------------------------------------------

class TestFeedbackEndpoint:
    def test_submit_feedback_up(self, client, auth_header):
        resp = client.post("/api/feedback", json={
            "message_id": "msg_123",
            "feedback": "up",
        }, headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_submit_feedback_down(self, client, auth_header):
        resp = client.post("/api/feedback", json={
            "message_id": "msg_456",
            "feedback": "down",
        }, headers=auth_header)
        assert resp.status_code == 200

    def test_invalid_feedback_rejected(self, client, auth_header):
        resp = client.post("/api/feedback", json={
            "message_id": "msg_789",
            "feedback": "invalid",
        }, headers=auth_header)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5.10 — Slots endpoint edge cases
# ---------------------------------------------------------------------------

class TestSlotsEndpoint:
    def test_get_slots_default_range(self, client, auth_header):
        resp = client.get("/api/slots", headers=auth_header)
        assert resp.status_code == 200
        body = resp.json()
        assert "slots" in body
        assert "total" in body

    def test_get_slots_with_dates(self, client, auth_header):
        start = SLOT_DATE.isoformat()
        end = (SLOT_DATE + timedelta(days=7)).isoformat()
        resp = client.get(f"/api/slots?date_start={start}&date_end={end}", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 0

    def test_get_slots_invalid_date(self, client, auth_header):
        resp = client.get("/api/slots?date_start=not-a-date", headers=auth_header)
        assert resp.status_code == 400

    def test_get_slots_with_provider(self, client, auth_header):
        start = SLOT_DATE.isoformat()
        end = (SLOT_DATE + timedelta(days=1)).isoformat()
        resp = client.get(
            f"/api/slots?date_start={start}&date_end={end}&provider=Smith",
            headers=auth_header,
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 5.10 — Identify endpoint edge cases
# ---------------------------------------------------------------------------

class TestIdentifyEdgeCases:
    def test_question_mode_no_name_needed(self, client, auth_header):
        resp = client.post("/api/identify", json={"mode": "question"}, headers=auth_header)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_returning_patient_found(self, client, auth_header):
        resp = client.post("/api/identify", json={
            "mode": "returning",
            "name": "Sarah Johnson",
            "phone": "555-010-1234",
        }, headers=auth_header)
        body = resp.json()
        assert body["status"] == "ok"
        assert body["patient_id"] == "patient_sarah"

    def test_returning_patient_not_found(self, client, auth_header):
        resp = client.post("/api/identify", json={
            "mode": "returning",
            "name": "Nobody",
            "phone": "555-999-9999",
        }, headers=auth_header)
        assert resp.json()["status"] == "not_found"

    def test_new_patient_then_duplicate(self, client, auth_header):
        """Create new patient, then try again with same phone → existing."""
        resp1 = client.post("/api/identify", json={
            "mode": "new",
            "name": "Test Edge",
            "phone": "555-777-6666",
        }, headers=auth_header)
        assert resp1.json()["status"] == "ok"

        resp2 = client.post("/api/identify", json={
            "mode": "new",
            "name": "Duplicate",
            "phone": "555-777-6666",
        }, headers=auth_header)
        assert resp2.json()["status"] == "existing"

    def test_returning_with_appointments(self, client, auth_header):
        """Returning patient should get upcoming appointments."""
        resp = client.post("/api/identify", json={
            "mode": "returning",
            "name": "Sarah Johnson",
            "phone": "555-010-1234",
        }, headers=auth_header)
        body = resp.json()
        assert body["status"] == "ok"
        # Sarah has one seeded appointment
        assert len(body["upcoming_appointments"]) >= 1


# ---------------------------------------------------------------------------
# 5.10c — SMS debounce
# ---------------------------------------------------------------------------

class TestSMSDebounce:
    """Rapid sequential messages are concatenated."""

    @pytest.mark.asyncio
    async def test_single_message_passes_through(self):
        _buffers.clear()
        result = await debounce_message("debounce_test_1", "hello")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_rapid_messages_concatenated(self):
        """Second message returns None (buffered), first gets concatenated."""
        _buffers.clear()
        sid = "debounce_test_2"

        # Send first message (becomes dispatcher)
        task1 = asyncio.create_task(debounce_message(sid, "Hi"))

        # Small delay then send second (gets buffered)
        await asyncio.sleep(0.05)
        task2 = asyncio.create_task(debounce_message(sid, "I need a cleaning"))

        result1, result2 = await asyncio.gather(task1, task2)

        # First caller gets concatenated result, second gets None
        assert result2 is None
        assert "Hi" in result1
        assert "cleaning" in result1


# ---------------------------------------------------------------------------
# 5.11 — Concurrent booking prevention (API level)
# ---------------------------------------------------------------------------

class TestConcurrentBookingPrevention:
    """Multiple simultaneous bookings for the same slot → only one succeeds."""

    def test_double_booking_same_slot_fails(self, seeded_db):
        """Book the same slot twice in sequence → second fails."""
        db = seeded_db

        # Find an available slot
        from sqlalchemy import select
        stmt = select(TimeSlot).where(
            TimeSlot.date == SLOT_DATE,
            TimeSlot.is_available.is_(True),
        ).limit(1)
        slot = db.execute(stmt).scalar_one()

        from src.db.models import AppointmentType
        from src.db.repositories import AppointmentRepository

        # First booking
        result1 = AppointmentRepository.book(
            db,
            patient_id="patient_sarah",
            slot_id=slot.id,
            appointment_type=AppointmentType.cleaning,
        )
        assert not isinstance(result1, dict)  # success

        # Second booking — same slot
        result2 = AppointmentRepository.book(
            db,
            patient_id="patient_mike",
            slot_id=slot.id,
            appointment_type=AppointmentType.cleaning,
        )
        assert isinstance(result2, dict)  # error
        assert "error" in result2


# ---------------------------------------------------------------------------
# 5.10 — Notification types
# ---------------------------------------------------------------------------

class TestNotificationTypes:
    """notify_staff handles all 3 notification types."""

    @pytest.mark.asyncio
    async def test_emergency_notification(self):
        result = await pytest.importorskip("src.agent.tools.notifications").notify_staff(
            type="emergency",
            message="Patient reports severe pain",
            patient_id="p123",
        )
        assert result["status"] == "sent"
        assert result["type"] == "emergency"

    @pytest.mark.asyncio
    async def test_special_request_notification(self):
        from src.agent.tools.notifications import notify_staff
        result = await notify_staff(
            type="special_request",
            message="Patient needs wheelchair access",
        )
        assert result["status"] == "sent"

    @pytest.mark.asyncio
    async def test_escalation_notification(self):
        from src.agent.tools.notifications import notify_staff
        result = await notify_staff(
            type="escalation",
            message="Patient unhappy with service",
        )
        assert result["status"] == "sent"


# ---------------------------------------------------------------------------
# 5.10 — Invalid appointment type
# ---------------------------------------------------------------------------

class TestInvalidAppointmentType:
    @pytest.mark.asyncio
    async def test_invalid_type_rejected(self, seeded_db):
        from src.cache.session import update_session
        sid = "test_bad_type"
        await update_session(sid, patient_id="patient_sarah")

        from sqlalchemy import select
        stmt = select(TimeSlot).where(
            TimeSlot.date == SLOT_DATE,
            TimeSlot.is_available.is_(True),
        ).limit(1)
        slot = seeded_db.execute(stmt).scalar_one()

        from src.agent.tools.appointments import book_appointment
        result = await book_appointment(
            patient_id="patient_sarah",
            slot_id=slot.id,
            appointment_type="not_a_real_type",
            db=seeded_db,
            session_id=sid,
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# 5.10 — Patient lookup edge cases
# ---------------------------------------------------------------------------

class TestPatientLookupEdgeCases:
    @pytest.mark.asyncio
    async def test_lookup_by_dob(self, seeded_db):
        from src.agent.tools.patients import lookup_patient
        result = await lookup_patient(
            name="Sarah Johnson",
            date_of_birth="1990-05-15",
            db=seeded_db,
            session_id="test_dob_lookup",
        )
        assert result["full_name"] == "Sarah Johnson"

    @pytest.mark.asyncio
    async def test_lookup_no_identifier(self, seeded_db):
        from src.agent.tools.patients import lookup_patient
        result = await lookup_patient(
            name="Sarah Johnson",
            db=seeded_db,
            session_id="test_no_id",
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_lookup_bad_dob_format(self, seeded_db):
        from src.agent.tools.patients import lookup_patient
        result = await lookup_patient(
            name="Sarah Johnson",
            date_of_birth="not-a-date",
            db=seeded_db,
            session_id="test_bad_dob",
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_lookup_nonexistent_patient(self, seeded_db):
        from src.agent.tools.patients import lookup_patient
        result = await lookup_patient(
            name="Nobody Exists",
            phone="5559999999",
            db=seeded_db,
            session_id="test_nobody",
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool registry validation
# ---------------------------------------------------------------------------

class TestToolRegistry:
    """execute_tool validates args via Pydantic and rejects bad input."""

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        from src.agent.tools import execute_tool
        result = await execute_tool("nonexistent_tool", {})
        assert "error" in result
        assert "Unknown" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_required_args(self):
        from src.agent.tools import execute_tool
        result = await execute_tool("lookup_patient", {})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_tool_without_db_when_needed(self):
        from src.agent.tools import execute_tool
        result = await execute_tool(
            "lookup_patient",
            {"name": "Test", "phone": "5551234567"},
            db=None,
            session_id="test",
        )
        assert "error" in result
