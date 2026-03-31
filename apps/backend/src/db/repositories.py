"""Repository layer — encapsulates all database queries.

Each repository is a thin, stateless class that receives a SQLAlchemy
``Session`` and exposes domain-focused methods.  Write operations catch
``IntegrityError``, retry once, and return a descriptive error dict on
second failure.
"""

from __future__ import annotations

import functools
import logging
from datetime import date, datetime, time, timezone
from typing import Literal

from sqlalchemy import select, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from src.db.models import (
    Appointment,
    AppointmentStatus,
    AppointmentType,
    ConversationLog,
    Patient,
    TimeSlot,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape_like(value: str) -> str:
    """Escape LIKE metacharacters so user input can't wildcard-match all rows.

    ``%`` and ``_`` are the two special characters in SQL LIKE patterns.
    """
    return value.replace("%", r"\%").replace("_", r"\_")


def _retry_on_integrity(fn):
    """Decorator: catch IntegrityError, retry once, then return error dict."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # Static methods have no self, so db is always a keyword arg.
        db: Session | None = kwargs.get("db")
        if db is None:
            # Fallback: check positional args (first positional for @staticmethod)
            for arg in args:
                if isinstance(arg, Session):
                    db = arg
                    break

        for attempt in range(2):
            try:
                return fn(*args, **kwargs)
            except IntegrityError as exc:
                if db is not None:
                    db.rollback()
                if attempt == 0:
                    logger.warning(
                        "IntegrityError on %s (attempt 1), retrying.",
                        fn.__name__,
                    )
                    continue
                # Log only the constraint name, not the full SQL (may contain PHI)
                logger.error(
                    "IntegrityError on %s (attempt 2), giving up: %s",
                    fn.__name__,
                    getattr(exc.orig, "args", ["unknown"])[0] if exc.orig else "unknown",
                )
                return {"error": f"Write conflict in {fn.__name__}. Please try again."}
        return None  # unreachable, keeps mypy happy

    return wrapper


# ---------------------------------------------------------------------------
# PatientRepository
# ---------------------------------------------------------------------------

class PatientRepository:
    """CRUD operations for the patients table."""

    @staticmethod
    def find_by_id(db: Session, patient_id: str) -> Patient | None:
        return db.get(Patient, patient_id)

    @staticmethod
    def find_by_name_and_phone(
        db: Session, full_name: str, phone: str
    ) -> Patient | None:
        escaped = _escape_like(full_name)
        stmt = select(Patient).where(
            and_(
                Patient.full_name.ilike(f"%{escaped}%", escape="\\"),
                Patient.phone == phone,
            )
        )
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def find_by_name_and_dob(
        db: Session, full_name: str, date_of_birth: date
    ) -> Patient | None:
        escaped = _escape_like(full_name)
        stmt = select(Patient).where(
            and_(
                Patient.full_name.ilike(f"%{escaped}%", escape="\\"),
                Patient.date_of_birth == date_of_birth,
            )
        )
        # Use .first() — name+dob is not unique, so multiple matches possible
        return db.execute(stmt).scalars().first()

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
        logger.info("Created patient %s", patient.id)
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
        provider_name: str | None = None,
    ) -> list[TimeSlot]:
        """Return available slots in a date range, optionally filtered by
        morning (before noon) or afternoon (noon onward), and/or provider."""
        conditions = [
            TimeSlot.date >= date_start,
            TimeSlot.date <= date_end,
            TimeSlot.is_available.is_(True),
        ]

        if time_pref == "morning":
            conditions.append(TimeSlot.start_time < time(12, 0))
        elif time_pref == "afternoon":
            conditions.append(TimeSlot.start_time >= time(12, 0))

        if provider_name:
            conditions.append(
                TimeSlot.provider_name.ilike(
                    f"%{_escape_like(provider_name)}%", escape="\\"
                )
            )

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
        ``target_date`` with the same provider (useful for family bookings)."""
        slots = SlotRepository.get_available(db, target_date, target_date)
        groups: list[list[TimeSlot]] = []
        for i in range(len(slots) - count + 1):
            group = slots[i : i + count]
            is_consecutive = all(
                group[j].end_time == group[j + 1].start_time
                and group[j].provider_name == group[j + 1].provider_name
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
    def find_by_id(db: Session, appointment_id: str) -> Appointment | None:
        return db.get(Appointment, appointment_id)

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
        if appt.status != AppointmentStatus.scheduled:
            return {
                "error": f"Only scheduled appointments can be cancelled "
                         f"(current status: {appt.status.value})."
            }

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
            .options(joinedload(Appointment.slot))
            .where(and_(*conditions))
            .order_by(Appointment.created_at.desc())
        )
        return list(db.execute(stmt).scalars().unique().all())


# ---------------------------------------------------------------------------
# ConversationLogRepository
# ---------------------------------------------------------------------------

class ConversationLogRepository:
    """CRUD for conversation_logs — used when flushing Redis sessions."""

    @staticmethod
    def create(
        db: Session,
        *,
        session_id: str,
        messages: str,
        patient_id: str | None = None,
        summary: str | None = None,
    ) -> ConversationLog:
        log = ConversationLog(
            session_id=session_id,
            patient_id=patient_id,
            messages=messages,
            summary=summary,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        logger.info("Created conversation log for session %s", session_id)
        return log

    @staticmethod
    def end_conversation(
        db: Session,
        session_id: str,
        summary: str | None = None,
    ) -> ConversationLog | None:
        """Mark a conversation as ended and optionally add a summary."""
        stmt = select(ConversationLog).where(
            ConversationLog.session_id == session_id
        )
        log = db.execute(stmt).scalars().first()
        if log is None:
            return None
        log.ended_at = datetime.now(timezone.utc)
        if summary is not None:
            log.summary = summary
        db.commit()
        db.refresh(log)
        return log

    @staticmethod
    def find_by_session(db: Session, session_id: str) -> ConversationLog | None:
        stmt = select(ConversationLog).where(
            ConversationLog.session_id == session_id
        )
        return db.execute(stmt).scalars().first()

    @staticmethod
    def find_by_patient(
        db: Session, patient_id: str, limit: int = 10
    ) -> list[ConversationLog]:
        stmt = (
            select(ConversationLog)
            .where(ConversationLog.patient_id == patient_id)
            .order_by(ConversationLog.created_at.desc())
            .limit(limit)
        )
        return list(db.execute(stmt).scalars().all())
