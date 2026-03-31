"""Phase 3B — Concurrency safety verification tests.

Tests that prove:
- LLM semaphore limits concurrent calls
- Double-booking the same slot is prevented at the DB level
- Session locks prevent concurrent agent runs for the same session
"""

from __future__ import annotations

import asyncio
from datetime import date, time
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import (
    Appointment,
    AppointmentStatus,
    AppointmentType,
    Base,
    Patient,
    TimeSlot,
)
from src.db.repositories import AppointmentRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """In-memory SQLite DB with WAL mode and tables created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()

    # Seed: 1 patient, 1 available slot
    patient = Patient(id="pat1", full_name="Test Patient", phone="5551234567")
    slot = TimeSlot(
        id="slot1",
        date=date(2026, 4, 7),
        start_time=time(9, 0),
        end_time=time(9, 30),
        is_available=True,
        provider_name="Dr. Smith",
    )
    session.add_all([patient, slot])
    session.commit()

    yield session

    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# 3B.1 — LLM semaphore
# ---------------------------------------------------------------------------

class TestLLMSemaphore:
    """Verify the semaphore in llm.py limits concurrent Gemini calls."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Simulate concurrent calls and verify only MAX_CONCURRENT run at once."""
        from src.agent.llm import _get_semaphore

        # Get the semaphore (creates it if needed)
        sem = await _get_semaphore()

        # Track max concurrent entries
        active = 0
        max_active = 0

        async def simulated_call():
            nonlocal active, max_active
            async with sem:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.01)  # simulate API latency
                active -= 1

        # Launch more tasks than the semaphore allows
        tasks = [asyncio.create_task(simulated_call()) for _ in range(20)]
        await asyncio.gather(*tasks)

        # MAX_CONCURRENT_LLM_CALLS defaults to 10
        assert max_active <= 10
        assert max_active > 1  # prove parallelism happened
        assert active == 0     # all released


# ---------------------------------------------------------------------------
# 3B.2 — Double-booking prevention
# ---------------------------------------------------------------------------

class TestDoubleBookingPrevention:
    """Verify that booking the same slot twice is rejected at the DB level."""

    def test_second_booking_same_slot_fails(self, db: Session):
        """Two bookings for slot1 — first succeeds, second returns error."""
        result1 = AppointmentRepository.book(
            db,
            patient_id="pat1",
            slot_id="slot1",
            appointment_type=AppointmentType.cleaning,
        )
        # First booking succeeds
        assert isinstance(result1, Appointment)
        assert result1.status == AppointmentStatus.scheduled

        # Second booking for same slot should fail
        result2 = AppointmentRepository.book(
            db,
            patient_id="pat1",
            slot_id="slot1",
            appointment_type=AppointmentType.cleaning,
        )
        # Should return error dict, not raise
        assert isinstance(result2, dict)
        assert "error" in result2

    def test_slot_marked_unavailable_after_booking(self, db: Session):
        """After booking, the slot's is_available flag is False."""
        AppointmentRepository.book(
            db,
            patient_id="pat1",
            slot_id="slot1",
            appointment_type=AppointmentType.cleaning,
        )
        slot = db.get(TimeSlot, "slot1")
        assert slot.is_available is False

    def test_cancelled_slot_becomes_available(self, db: Session):
        """Cancelling frees the slot for rebooking."""
        appt = AppointmentRepository.book(
            db,
            patient_id="pat1",
            slot_id="slot1",
            appointment_type=AppointmentType.cleaning,
        )
        assert isinstance(appt, Appointment)

        # Cancel
        result = AppointmentRepository.cancel(db, appt.id)
        assert isinstance(result, Appointment)
        assert result.status == AppointmentStatus.cancelled

        # Slot is available again
        slot = db.get(TimeSlot, "slot1")
        assert slot.is_available is True

    def test_reschedule_atomic_slot_swap(self, db: Session):
        """Reschedule frees old slot and claims new slot atomically."""
        # Add a second slot
        slot2 = TimeSlot(
            id="slot2",
            date=date(2026, 4, 7),
            start_time=time(10, 0),
            end_time=time(10, 30),
            is_available=True,
            provider_name="Dr. Smith",
        )
        db.add(slot2)
        db.commit()

        # Book slot1
        appt = AppointmentRepository.book(
            db,
            patient_id="pat1",
            slot_id="slot1",
            appointment_type=AppointmentType.cleaning,
        )
        assert isinstance(appt, Appointment)

        # Reschedule to slot2
        result = AppointmentRepository.reschedule(db, appt.id, "slot2")
        assert isinstance(result, Appointment)

        # Old slot freed, new slot claimed
        old_slot = db.get(TimeSlot, "slot1")
        new_slot = db.get(TimeSlot, "slot2")
        assert old_slot.is_available is True
        assert new_slot.is_available is False


# ---------------------------------------------------------------------------
# 3B.3 — Session locks
# ---------------------------------------------------------------------------

class TestSessionLocks:
    """Verify Redis session locks prevent concurrent agent runs."""

    @pytest.mark.asyncio
    async def test_lock_acquired_and_released(self):
        """Basic lock lifecycle: acquire → hold → release."""
        from src.cache.session import acquire_session_lock, release_session_lock

        token = await acquire_session_lock("test_lock_session")
        assert token is not None

        # Release
        await release_session_lock("test_lock_session", token)

    @pytest.mark.asyncio
    async def test_second_lock_fails(self):
        """Second lock attempt for the same session returns None."""
        from src.cache.session import acquire_session_lock, release_session_lock

        token1 = await acquire_session_lock("test_contention")
        assert token1 is not None

        # Second attempt should fail
        token2 = await acquire_session_lock("test_contention")
        assert token2 is None

        # Cleanup
        await release_session_lock("test_contention", token1)

    @pytest.mark.asyncio
    async def test_wrong_token_does_not_release(self):
        """Releasing with wrong token does not free the lock."""
        from src.cache.session import acquire_session_lock, release_session_lock

        token = await acquire_session_lock("test_wrong_token")
        assert token is not None

        # Try releasing with wrong token
        await release_session_lock("test_wrong_token", "fake-token")

        # Lock should still be held — second acquire fails
        token2 = await acquire_session_lock("test_wrong_token")
        assert token2 is None

        # Cleanup with real token
        await release_session_lock("test_wrong_token", token)

    @pytest.mark.asyncio
    async def test_session_crud_basic(self):
        """Basic session get/update/clear lifecycle."""
        from src.cache.session import get_session, update_session, clear_session

        session = await get_session("test_crud")
        assert session["patient_id"] is None
        assert session["messages"] == []

        await update_session("test_crud", patient_id="p123", intent="returning")
        session = await get_session("test_crud")
        assert session["patient_id"] == "p123"
        assert session["intent"] == "returning"

        await clear_session("test_crud")
        session = await get_session("test_crud")
        assert session["patient_id"] is None  # fresh session
