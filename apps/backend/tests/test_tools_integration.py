"""Integration tests for agent tools — exercises tools against a real DB.

Covers TODO 5.1–5.9 core scenarios:
- New patient creation + booking
- Existing patient lookup + reschedule
- Emergency booking + staff notification
- Family booking (consecutive slots)
- No-insurance patient (practice info)
- Fully booked date → no availability
- Cancellation flow
- Patient appointments retrieval
- Knowledge base queries (practice info tool)

All tests use in-memory SQLite (no mocking of repositories or DB).
Session state uses the in-memory fallback (no Redis).
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from src.agent.tools.appointments import (
    book_appointment,
    cancel_appointment,
    get_available_slots,
    get_consecutive_slots,
    get_patient_appointments,
    reschedule_appointment,
)
from src.agent.tools.patients import create_patient, lookup_patient, update_patient
from src.agent.tools.notifications import notify_staff, get_notifications
from src.agent.tools.practice_info import get_practice_info
from src.cache.session import update_session
from src.db.models import AppointmentStatus, TimeSlot

from tests.conftest import PROVIDER, SLOT_DATE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _force_memory_fallback(monkeypatch):
    """Force in-memory session fallback for all tests (no Redis)."""
    monkeypatch.setattr("src.cache.redis_client._client", None)
    monkeypatch.setattr("src.cache.redis_client._pool", None)
    monkeypatch.setattr("src.cache.redis_client._using_fallback", True)


# ---------------------------------------------------------------------------
# 5.1 — New patient full booking flow
# ---------------------------------------------------------------------------

class TestNewPatientBookingFlow:
    """New patient → create → get slots → pick one → book."""

    @pytest.mark.asyncio
    async def test_create_patient_and_book(self, seeded_db):
        db = seeded_db
        sid = "test_session_new"

        # Create a new patient
        result = await create_patient(
            full_name="Jane Doe",
            phone="5559998888",
            date_of_birth="1995-07-20",
            insurance_name="Aetna",
            db=db,
            session_id=sid,
        )
        assert "error" not in result
        assert result["full_name"] == "Jane Doe"
        patient_id = result["id"]

        # Get available slots
        start = SLOT_DATE.isoformat()
        end = (SLOT_DATE + timedelta(days=1)).isoformat()
        slots_result = await get_available_slots(
            date_start=start,
            date_end=end,
            db=db,
        )
        assert "error" not in slots_result
        assert slots_result["total_available"] > 0
        slot_id = slots_result["slots"][0]["id"]

        # Book appointment
        book_result = await book_appointment(
            patient_id=patient_id,
            slot_id=slot_id,
            appointment_type="cleaning",
            db=db,
            session_id=sid,
        )
        assert "confirmation" in book_result
        assert book_result["confirmation"]["appointment_type"] == "cleaning"
        assert book_result["confirmation"]["patient_id"] == patient_id

        # Verify slot is now unavailable
        slot = db.get(TimeSlot, slot_id)
        assert slot.is_available is False

    @pytest.mark.asyncio
    async def test_create_patient_duplicate_phone(self, seeded_db):
        """Creating a patient with an existing phone fails."""
        result = await create_patient(
            full_name="Duplicate",
            phone="5550101234",  # Sarah's phone
            db=seeded_db,
            session_id="dup_session",
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_book_requires_patient_identification(self, seeded_db):
        """Booking without identifying a patient first fails."""
        sid = "unidentified_session"
        slots_result = await get_available_slots(
            date_start=SLOT_DATE.isoformat(),
            date_end=SLOT_DATE.isoformat(),
            db=seeded_db,
        )
        slot_id = slots_result["slots"][0]["id"]

        result = await book_appointment(
            patient_id="patient_sarah",
            slot_id=slot_id,
            appointment_type="cleaning",
            db=seeded_db,
            session_id=sid,
        )
        assert "error" in result
        assert "identified" in result["error"].lower() or "Patient" in result["error"]


# ---------------------------------------------------------------------------
# 5.2 — Existing patient reschedule
# ---------------------------------------------------------------------------

class TestExistingPatientReschedule:
    """Sarah Johnson → lookup → view appointments → reschedule."""

    @pytest.mark.asyncio
    async def test_lookup_and_reschedule(self, seeded_db):
        db = seeded_db
        sid = "test_session_reschedule"

        # Lookup Sarah
        result = await lookup_patient(
            name="Sarah Johnson",
            phone="5550101234",
            db=db,
            session_id=sid,
        )
        assert result["full_name"] == "Sarah Johnson"
        patient_id = result["id"]

        # Get her appointments
        appts = await get_patient_appointments(
            patient_id=patient_id,
            db=db,
            session_id=sid,
        )
        assert len(appts["appointments"]) >= 1
        appt_id = appts["appointments"][0]["id"]

        # Find a new slot
        slots_result = await get_available_slots(
            date_start=SLOT_DATE.isoformat(),
            date_end=(SLOT_DATE + timedelta(days=1)).isoformat(),
            db=db,
        )
        new_slot_id = slots_result["slots"][0]["id"]

        # Get old slot id before reschedule
        from src.db.repositories import AppointmentRepository
        old_appt = AppointmentRepository.find_by_id(db, appt_id)
        old_slot_id = old_appt.slot_id

        # Reschedule
        resc = await reschedule_appointment(
            appointment_id=appt_id,
            new_slot_id=new_slot_id,
            db=db,
            session_id=sid,
        )
        assert "updated_appointment" in resc

        # Verify old slot is freed
        old_slot = db.get(TimeSlot, old_slot_id)
        assert old_slot.is_available is True

        # Verify new slot is claimed
        new_slot = db.get(TimeSlot, new_slot_id)
        assert new_slot.is_available is False

    @pytest.mark.asyncio
    async def test_reschedule_nonexistent_appointment(self, seeded_db):
        sid = "test_reschedule_fail"
        await update_session(sid, patient_id="patient_sarah")

        result = await reschedule_appointment(
            appointment_id="nonexistent",
            new_slot_id="also_nonexistent",
            db=seeded_db,
            session_id=sid,
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# 5.3 — Emergency booking + staff notification
# ---------------------------------------------------------------------------

class TestEmergencyBooking:
    """Emergency: empathy → slot → notify staff."""

    @pytest.mark.asyncio
    async def test_emergency_booking_and_notify(self, seeded_db):
        db = seeded_db
        sid = "test_emergency"
        await update_session(sid, patient_id="patient_sarah")

        # Get earliest slot
        slots = await get_available_slots(
            date_start=SLOT_DATE.isoformat(),
            date_end=SLOT_DATE.isoformat(),
            db=db,
        )
        slot_id = slots["slots"][0]["id"]

        # Book as emergency
        book_result = await book_appointment(
            patient_id="patient_sarah",
            slot_id=slot_id,
            appointment_type="emergency",
            notes="cracked tooth, severe pain",
            db=db,
            session_id=sid,
        )
        assert "confirmation" in book_result
        assert book_result["confirmation"]["appointment_type"] == "emergency"

        # Notify staff
        notify_result = await notify_staff(
            type="emergency",
            message="Patient Sarah Johnson reports cracked tooth with severe pain. Booked emergency slot.",
            patient_id="patient_sarah",
        )
        assert notify_result["status"] == "sent"
        assert notify_result["type"] == "emergency"

        # Verify notification was stored
        notifications = get_notifications()
        assert any(n["type"] == "emergency" for n in notifications)


# ---------------------------------------------------------------------------
# 5.4 — Family booking (consecutive slots)
# ---------------------------------------------------------------------------

class TestFamilyBooking:
    """Parent + 2 kids → find consecutive slots → book all 3."""

    @pytest.mark.asyncio
    async def test_consecutive_slots_and_book(self, seeded_db):
        db = seeded_db

        # Find 3 consecutive slots
        consec = await get_consecutive_slots(
            target_date=SLOT_DATE.isoformat(),
            count=3,
            db=db,
        )
        assert len(consec["groups"]) > 0
        group = consec["groups"][0]
        slot_ids = [s["id"] for s in group["slots"]]

        # Create family members
        patients = []
        for i, (name, phone) in enumerate([
            ("Mom Smith", "5551110001"),
            ("Kid One Smith", "5551110002"),
            ("Kid Two Smith", "5551110003"),
        ]):
            sid = f"family_session_{i}"
            p = await create_patient(full_name=name, phone=phone, db=db, session_id=sid)
            assert "error" not in p
            patients.append((p["id"], sid))

        # Book each on their slot
        for (pid, sid), slot_id in zip(patients, slot_ids):
            result = await book_appointment(
                patient_id=pid,
                slot_id=slot_id,
                appointment_type="cleaning",
                db=db,
                session_id=sid,
            )
            assert "confirmation" in result

        # All 3 slots should be unavailable
        for slot_id in slot_ids:
            slot = db.get(TimeSlot, slot_id)
            assert slot.is_available is False


# ---------------------------------------------------------------------------
# 5.5 — No insurance patient (practice info)
# ---------------------------------------------------------------------------

class TestNoInsurancePatient:
    """Patient without insurance → practice info has self-pay options."""

    @pytest.mark.asyncio
    async def test_practice_info_has_self_pay(self):
        info = await get_practice_info()
        assert "self_pay_options" in info
        assert "15%" in info["self_pay_options"]["discount"]
        assert "CareCredit" in info["self_pay_options"]["financing"]
        assert "$299" in info["self_pay_options"]["membership"]

    @pytest.mark.asyncio
    async def test_practice_info_has_insurance_list(self):
        info = await get_practice_info()
        assert "Delta Dental" in info["insurance_accepted"]
        assert "Aetna" in info["insurance_accepted"]


# ---------------------------------------------------------------------------
# 5.6 — Fully booked date → no availability
# ---------------------------------------------------------------------------

class TestFullyBookedDate:
    """All slots on a date are booked → returns 0 available."""

    @pytest.mark.asyncio
    async def test_no_slots_available(self, seeded_db):
        db = seeded_db

        # Mark all slots on SLOT_DATE as unavailable
        from sqlalchemy import select, and_
        from src.db.models import TimeSlot as TS
        stmt = select(TS).where(and_(TS.date == SLOT_DATE, TS.is_available.is_(True)))
        available_slots = list(db.execute(stmt).scalars().all())
        for slot in available_slots:
            slot.is_available = False
        db.commit()

        # Query — should get 0
        result = await get_available_slots(
            date_start=SLOT_DATE.isoformat(),
            date_end=SLOT_DATE.isoformat(),
            db=db,
        )
        assert result["total_available"] == 0
        assert "No available" in result.get("message", "")


# ---------------------------------------------------------------------------
# 5.7 — Subjective date expressions
# ---------------------------------------------------------------------------

class TestSubjectiveDates:
    """Natural language dates → parsed correctly by get_available_slots."""

    @pytest.mark.asyncio
    async def test_iso_dates_passthrough(self, seeded_db):
        result = await get_available_slots(
            date_start=SLOT_DATE.isoformat(),
            date_end=(SLOT_DATE + timedelta(days=7)).isoformat(),
            db=seeded_db,
        )
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_invalid_date_returns_error(self, seeded_db):
        result = await get_available_slots(
            date_start="not-a-date-and-not-a-phrase-xyzzy",
            date_end="also-not-a-date-xyzzy",
            db=seeded_db,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_reversed_dates_returns_error(self, seeded_db):
        result = await get_available_slots(
            date_start=(SLOT_DATE + timedelta(days=7)).isoformat(),
            date_end=SLOT_DATE.isoformat(),
            db=seeded_db,
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# 5.8 — Existing patient cancellation
# ---------------------------------------------------------------------------

class TestExistingPatientCancellation:
    """Sarah → lookup → cancel → slot freed."""

    @pytest.mark.asyncio
    async def test_cancel_appointment(self, seeded_db):
        db = seeded_db
        sid = "test_cancel"

        # Lookup Sarah
        patient = await lookup_patient(
            name="Sarah Johnson",
            phone="5550101234",
            db=db,
            session_id=sid,
        )
        patient_id = patient["id"]

        # Get appointments
        appts = await get_patient_appointments(
            patient_id=patient_id,
            db=db,
            session_id=sid,
        )
        assert len(appts["appointments"]) >= 1
        appt_id = appts["appointments"][0]["id"]

        # Get slot id before cancel
        from src.db.repositories import AppointmentRepository
        appt_obj = AppointmentRepository.find_by_id(db, appt_id)
        slot_id = appt_obj.slot_id

        # Cancel
        result = await cancel_appointment(
            appointment_id=appt_id,
            db=db,
            session_id=sid,
        )
        assert "cancelled" in result
        assert result["cancelled"]["status"] == "cancelled"

        # Slot is free again
        slot = db.get(TimeSlot, slot_id)
        assert slot.is_available is True

        # Appointment list is now empty
        appts2 = await get_patient_appointments(
            patient_id=patient_id,
            db=db,
            session_id=sid,
        )
        assert len(appts2["appointments"]) == 0

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled(self, seeded_db):
        """Cancelling an already-cancelled appointment fails."""
        db = seeded_db
        sid = "test_double_cancel"
        await update_session(sid, patient_id="patient_sarah")

        # Cancel once
        await cancel_appointment(appointment_id="appt_sarah_1", db=db, session_id=sid)

        # Cancel again
        result = await cancel_appointment(
            appointment_id="appt_sarah_1",
            db=db,
            session_id=sid,
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# 5.9 — Knowledge base queries (practice info)
# ---------------------------------------------------------------------------

class TestKnowledgeBaseQueries:
    """Practice info returns correct static data."""

    @pytest.mark.asyncio
    async def test_hours(self):
        info = await get_practice_info()
        assert info["hours"]["Sunday"] == "Closed"
        assert "8:00 AM" in info["hours"]["Monday"]

    @pytest.mark.asyncio
    async def test_providers(self):
        info = await get_practice_info()
        providers = [p["name"] for p in info["providers"]]
        assert "Dr. Sarah Smith" in providers

    @pytest.mark.asyncio
    async def test_cancellation_policy(self):
        info = await get_practice_info()
        assert "24 hours" in info["cancellation_policy"]

    @pytest.mark.asyncio
    async def test_accessibility(self):
        info = await get_practice_info()
        assert any("wheelchair" in a.lower() for a in info["accessibility"])


# ---------------------------------------------------------------------------
# Slot pagination
# ---------------------------------------------------------------------------

class TestSlotPagination:
    """get_available_slots paginates to first 5 results."""

    @pytest.mark.asyncio
    async def test_pagination_shows_first_five(self, seeded_db):
        result = await get_available_slots(
            date_start=SLOT_DATE.isoformat(),
            date_end=(SLOT_DATE + timedelta(days=1)).isoformat(),
            db=seeded_db,
        )
        # seeded_db has 10 + 5 = 15 slots minus 1 booked = 14 available
        assert len(result["slots"]) <= 5
        assert result["total_available"] > 5


# ---------------------------------------------------------------------------
# Patient update flow
# ---------------------------------------------------------------------------

class TestPatientUpdate:
    """Update patient DOB and insurance after creation."""

    @pytest.mark.asyncio
    async def test_update_dob_and_insurance(self, seeded_db):
        db = seeded_db
        sid = "test_update"
        await update_session(sid, patient_id="patient_mike")

        result = await update_patient(
            patient_id="patient_mike",
            date_of_birth="1985-03-20",
            insurance_name="Cigna",
            db=db,
            session_id=sid,
        )
        assert "error" not in result
        assert result["insurance_name"] == "Cigna"
        assert result["date_of_birth"] == "1985-03-20"

    @pytest.mark.asyncio
    async def test_update_wrong_patient(self, seeded_db):
        """Cannot update a patient that doesn't match the session."""
        db = seeded_db
        sid = "test_wrong_update"
        await update_session(sid, patient_id="patient_sarah")

        result = await update_patient(
            patient_id="patient_mike",
            insurance_name="Cigna",
            db=db,
            session_id=sid,
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# Cross-patient booking prevention
# ---------------------------------------------------------------------------

class TestCrossPatientPrevention:
    """Cannot book for a different patient than identified in session."""

    @pytest.mark.asyncio
    async def test_booking_wrong_patient_blocked(self, seeded_db):
        db = seeded_db
        sid = "test_cross_patient"
        await update_session(sid, patient_id="patient_sarah")

        slots = await get_available_slots(
            date_start=SLOT_DATE.isoformat(),
            date_end=SLOT_DATE.isoformat(),
            db=db,
        )
        slot_id = slots["slots"][0]["id"]

        result = await book_appointment(
            patient_id="patient_mike",  # wrong patient
            slot_id=slot_id,
            appointment_type="cleaning",
            db=db,
            session_id=sid,
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_appointments_wrong_patient_blocked(self, seeded_db):
        db = seeded_db
        sid = "test_cross_appts"
        await update_session(sid, patient_id="patient_sarah")

        result = await get_patient_appointments(
            patient_id="patient_mike",
            db=db,
            session_id=sid,
        )
        assert "error" in result
