"""Repository layer — encapsulates all database queries.

Each repository is a thin, stateless class that receives a SQLAlchemy
``Session`` and exposes domain-focused methods.  Write operations catch
``IntegrityError``, retry once, and return a descriptive error dict on
second failure.
"""

from __future__ import annotations

import logging
from datetime import date, time
from typing import Literal

from sqlalchemy import select, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models import (
    Appointment,
    AppointmentStatus,
    AppointmentType,
    Patient,
    TimeSlot,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _retry_on_integrity(fn):
    """Decorator: catch IntegrityError, retry once, then return error dict."""

    def wrapper(*args, **kwargs):
        for attempt in range(2):
            try:
                return fn(*args, **kwargs)
            except IntegrityError as exc:
                # Get the session — first positional arg after self is db
                db: Session = args[1] if len(args) > 1 else kwargs.get("db")
                if db is not None:
                    db.rollback()
                if attempt == 0:
                    logger.warning(
                        "IntegrityError on %s (attempt 1), retrying: %s",
                        fn.__name__,
                        exc,
                    )
                    continue
                logger.error(
                    "IntegrityError on %s (attempt 2), giving up: %s",
                    fn.__name__,
                    exc,
                )
                return {"error": f"Write conflict in {fn.__name__}: {exc.orig}"}
        return None  # unreachable, keeps mypy happy

    return wrapper


# ---------------------------------------------------------------------------
# PatientRepository
# ---------------------------------------------------------------------------

class PatientRepository:
    """CRUD operations for the patients table."""

    @staticmethod
    def find_by_name_and_phone(
        db: Session, full_name: str, phone: str
    ) -> Patient | None:
        stmt = select(Patient).where(
            and_(
                Patient.full_name.ilike(f"%{full_name}%"),
                Patient.phone == phone,
            )
        )
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def find_by_name_and_dob(
        db: Session, full_name: str, date_of_birth: date
    ) -> Patient | None:
        stmt = select(Patient).where(
            and_(
                Patient.full_name.ilike(f"%{full_name}%"),
                Patient.date_of_birth == date_of_birth,
            )
        )
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def find_by_phone(db: Session, phone: str) -> Patient | None:
        stmt = select(Patient).where(Patient.phone == phone)
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    @_retry_on_integrity
    def create(
        db: Session,
        *,
        full_name: str,
        phone: str,
        date_of_birth: date | None = None,
        insurance_name: str | None = None,
    ) -> Patient | dict:
        patient = Patient(
            full_name=full_name,
            phone=phone,
            date_of_birth=date_of_birth,
            insurance_name=insurance_name,
        )
        db.add(patient)
        db.commit()
        db.refresh(patient)
        logger.info("Created patient %s (%s)", patient.id, full_name)
        return patient


# ---------------------------------------------------------------------------
# SlotRepository
# ---------------------------------------------------------------------------

class SlotRepository:
    """Queries for the time_slots table."""

    @staticmethod
    def get_available(
        db: Session,
        date_start: date,
        date_end: date,
        time_pref: Literal["morning", "afternoon", "any"] = "any",
    ) -> list[TimeSlot]:
        """Return available slots in a date range, optionally filtered by
        morning (before noon) or afternoon (noon onward)."""
        conditions = [
            TimeSlot.date >= date_start,
            TimeSlot.date <= date_end,
            TimeSlot.is_available.is_(True),
        ]

        if time_pref == "morning":
            conditions.append(TimeSlot.start_time < time(12, 0))
        elif time_pref == "afternoon":
            conditions.append(TimeSlot.start_time >= time(12, 0))

        stmt = (
            select(TimeSlot)
            .where(and_(*conditions))
            .order_by(TimeSlot.date, TimeSlot.start_time)
        )
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def get_consecutive(
        db: Session, target_date: date, count: int = 2
    ) -> list[list[TimeSlot]]:
        """Find groups of ``count`` consecutive available slots on
        ``target_date`` (useful for longer appointments)."""
        slots = SlotRepository.get_available(db, target_date, target_date)
        groups: list[list[TimeSlot]] = []
        for i in range(len(slots) - count + 1):
            group = slots[i : i + count]
            # Ensure all slots are on the same date
            same_date = all(s.date == group[0].date for s in group)
            if not same_date:
                continue
            # Check that each slot's end_time equals the next slot's start_time
            is_consecutive = all(
                group[j].end_time == group[j + 1].start_time
                for j in range(len(group) - 1)
            )
            if is_consecutive:
                groups.append(group)
        return groups

    @staticmethod
    def find_by_id(db: Session, slot_id: str) -> TimeSlot | None:
        return db.get(TimeSlot, slot_id)


# ---------------------------------------------------------------------------
# AppointmentRepository
# ---------------------------------------------------------------------------

class AppointmentRepository:
    """Transactional operations for appointments."""

    @staticmethod
    @_retry_on_integrity
    def book(
        db: Session,
        *,
        patient_id: str,
        slot_id: str,
        appointment_type: AppointmentType,
        notes: str | None = None,
    ) -> Appointment | dict:
        """Book an appointment — marks the slot unavailable in one transaction."""
        slot = db.get(TimeSlot, slot_id)
        if slot is None:
            return {"error": f"Slot {slot_id} not found."}
        if not slot.is_available:
            return {"error": f"Slot {slot_id} is no longer available."}

        slot.is_available = False
        appointment = Appointment(
            patient_id=patient_id,
            slot_id=slot_id,
            appointment_type=appointment_type,
            notes=notes,
        )
        db.add(appointment)
        db.commit()
        db.refresh(appointment)
        logger.info(
            "Booked appointment %s for patient %s", appointment.id, patient_id
        )
        return appointment

    @staticmethod
    @_retry_on_integrity
    def cancel(db: Session, appointment_id: str) -> Appointment | dict:
        """Cancel an appointment and free its slot."""
        appt = db.get(Appointment, appointment_id)
        if appt is None:
            return {"error": f"Appointment {appointment_id} not found."}
        if appt.status == AppointmentStatus.cancelled:
            return {"error": "Appointment is already cancelled."}

        appt.status = AppointmentStatus.cancelled
        slot = db.get(TimeSlot, appt.slot_id)
        if slot is not None:
            slot.is_available = True

        db.commit()
        db.refresh(appt)
        logger.info("Cancelled appointment %s", appointment_id)
        return appt

    @staticmethod
    @_retry_on_integrity
    def reschedule(
        db: Session, appointment_id: str, new_slot_id: str
    ) -> Appointment | dict:
        """Atomic reschedule: free old slot, claim new slot, update appointment."""
        appt = db.get(Appointment, appointment_id)
        if appt is None:
            return {"error": f"Appointment {appointment_id} not found."}
        if appt.status != AppointmentStatus.scheduled:
            return {"error": "Only scheduled appointments can be rescheduled."}

        new_slot = db.get(TimeSlot, new_slot_id)
        if new_slot is None:
            return {"error": f"New slot {new_slot_id} not found."}
        if not new_slot.is_available:
            return {"error": f"Slot {new_slot_id} is no longer available."}

        # Free old slot
        old_slot = db.get(TimeSlot, appt.slot_id)
        if old_slot is not None:
            old_slot.is_available = True

        # Claim new slot
        new_slot.is_available = False
        appt.slot_id = new_slot_id

        db.commit()
        db.refresh(appt)
        logger.info(
            "Rescheduled appointment %s → slot %s", appointment_id, new_slot_id
        )
        return appt

    @staticmethod
    def get_patient_appointments(
        db: Session,
        patient_id: str,
        status: AppointmentStatus | None = None,
    ) -> list[Appointment]:
        conditions = [Appointment.patient_id == patient_id]
        if status is not None:
            conditions.append(Appointment.status == status)

        stmt = (
            select(Appointment)
            .where(and_(*conditions))
            .order_by(Appointment.created_at.desc())
        )
        return list(db.execute(stmt).scalars().all())
