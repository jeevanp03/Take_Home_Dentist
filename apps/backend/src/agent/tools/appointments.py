"""Appointment-related tools for the ReAct agent.

Functions
---------
- get_available_slots   -- find open time slots in a date range
- book_appointment      -- reserve a slot for a patient
- reschedule_appointment -- move an existing appointment to a new slot
- cancel_appointment    -- cancel a scheduled appointment
- get_patient_appointments -- list a patient's upcoming appointments
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time

from sqlalchemy.orm import Session

from src.cache.session import update_session
from src.db.models import AppointmentStatus, AppointmentType, Appointment, TimeSlot
from src.db.repositories import AppointmentRepository, SlotRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_PAGE_SIZE = 5


def _fmt_time(t: time) -> str:
    """Format a time object as '9:00 AM' (no leading zero, no seconds)."""
    dt = datetime.combine(date.today(), t)
    # Use lstrip to remove leading zero portably (%-I is GNU/macOS only)
    return dt.strftime("%I:%M %p").lstrip("0")


def _fmt_date(d: date) -> str:
    """Format a date object as 'Monday, April 7'."""
    # Use lstrip to remove leading zero portably (%-d is GNU/macOS only)
    day = str(d.day)
    return d.strftime(f"%A, %B {day}")


def _slot_to_dict(slot: TimeSlot) -> dict:
    return {
        "id": slot.id,
        "date": _fmt_date(slot.date),
        "start_time": _fmt_time(slot.start_time),
        "end_time": _fmt_time(slot.end_time),
        "provider_name": slot.provider_name,
    }


def _appointment_to_dict(appt: Appointment) -> dict:
    """Convert an Appointment (with its related slot loaded) to a clean dict."""
    slot: TimeSlot | None = appt.slot
    result: dict = {
        "id": appt.id,
        "appointment_type": appt.appointment_type.value,
        "status": appt.status.value,
    }
    if slot is not None:
        result.update({
            "date": _fmt_date(slot.date),
            "start_time": _fmt_time(slot.start_time),
            "end_time": _fmt_time(slot.end_time),
            "provider_name": slot.provider_name,
        })
    if appt.notes:
        result["notes"] = appt.notes
    return result


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

async def get_available_slots(
    date_start: str,
    date_end: str,
    time_preference: str = "any",
    provider_name: str | None = None,
    *,
    db: Session,
) -> dict:
    """Return available appointment slots within a date range.

    Paginates results to the first 5 slots and reports the total count so the
    LLM can tell the patient more are available.
    """
    try:
        start = date.fromisoformat(date_start)
        end = date.fromisoformat(date_end)
    except (ValueError, TypeError) as exc:
        logger.warning("Invalid date input for get_available_slots: %s", exc)
        return {"error": f"Invalid date format. Use YYYY-MM-DD. ({exc})"}

    if start > end:
        return {"error": "date_start must be on or before date_end."}

    slots = SlotRepository.get_available(
        db, start, end, time_pref=time_preference, provider_name=provider_name,
    )
    total = len(slots)
    page = slots[:_PAGE_SIZE]

    logger.info(
        "Found %d available slot(s) between %s and %s (showing %d).",
        total, date_start, date_end, len(page),
    )

    result: dict = {
        "slots": [_slot_to_dict(s) for s in page],
        "total_available": total,
    }
    if total > _PAGE_SIZE:
        result["message"] = (
            f"Showing first {_PAGE_SIZE} of {total} available slots. "
            "Ask the patient if they'd like to see more options."
        )
    elif total == 0:
        result["message"] = "No available slots found in the requested date range."

    return result


async def book_appointment(
    patient_id: str,
    slot_id: str,
    appointment_type: str,
    notes: str | None = None,
    *,
    db: Session,
    session_id: str,
) -> dict:
    """Book an appointment for a patient and update the session booking state."""
    # Validate patient_id matches the session's identified patient
    from src.cache.session import get_session
    session = await get_session(session_id)
    session_patient = session.get("patient_id")
    if not session_patient:
        return {"error": "Patient must be identified before booking. Use lookup_patient or create_patient first."}
    if session_patient != patient_id:
        logger.warning(
            "patient_id mismatch: session has %s but booking requested for %s",
            session_patient, patient_id,
        )
        return {"error": "Cannot book for a different patient than the one identified in this session."}

    # Validate appointment type
    try:
        appt_type = AppointmentType(appointment_type)
    except ValueError:
        valid = [t.value for t in AppointmentType]
        return {"error": f"Invalid appointment type '{appointment_type}'. Must be one of: {valid}"}

    result = AppointmentRepository.book(
        db,
        patient_id=patient_id,
        slot_id=slot_id,
        appointment_type=appt_type,
        notes=notes,
    )

    # Repository returns error dict on failure
    if isinstance(result, dict):
        logger.warning("Booking failed for patient %s, slot %s: %s", patient_id, slot_id, result)
        return result

    # Success — result is an Appointment ORM object
    appt: Appointment = result
    confirmation = _appointment_to_dict(appt)
    confirmation["patient_id"] = patient_id

    # Update Redis session with booking state
    await update_session(
        session_id,
        booking_state={
            "appointment_id": appt.id,
            "status": "confirmed",
        },
    )

    logger.info("Appointment %s booked for patient %s.", appt.id, patient_id)
    return {"confirmation": confirmation}


async def reschedule_appointment(
    appointment_id: str,
    new_slot_id: str,
    *,
    db: Session,
    session_id: str,
) -> dict:
    """Reschedule an existing appointment to a new time slot."""
    # Verify appointment belongs to the session's patient
    from src.cache.session import get_session
    session = await get_session(session_id)
    session_patient = session.get("patient_id")
    existing = AppointmentRepository.find_by_id(db, appointment_id)
    if existing and session_patient and existing.patient_id != session_patient:
        return {"error": "This appointment does not belong to the current patient."}

    result = AppointmentRepository.reschedule(db, appointment_id, new_slot_id)

    if isinstance(result, dict):
        logger.warning("Reschedule failed for appointment %s: %s", appointment_id, result)
        return result

    appt: Appointment = result
    updated = _appointment_to_dict(appt)

    # Keep booking_state consistent
    await update_session(
        session_id,
        booking_state={"appointment_id": appt.id, "status": "rescheduled"},
    )

    logger.info("Appointment %s rescheduled to slot %s.", appointment_id, new_slot_id)
    return {"updated_appointment": updated}


async def cancel_appointment(
    appointment_id: str,
    *,
    db: Session,
    session_id: str,
) -> dict:
    """Cancel a scheduled appointment and free its time slot."""
    # Verify appointment belongs to the session's patient
    from src.cache.session import get_session
    session = await get_session(session_id)
    session_patient = session.get("patient_id")
    existing = AppointmentRepository.find_by_id(db, appointment_id)
    if existing and session_patient and existing.patient_id != session_patient:
        return {"error": "This appointment does not belong to the current patient."}

    result = AppointmentRepository.cancel(db, appointment_id)

    if isinstance(result, dict):
        logger.warning("Cancellation failed for appointment %s: %s", appointment_id, result)
        return result

    appt: Appointment = result

    # Clear booking_state so the session doesn't reference a cancelled appointment
    await update_session(session_id, booking_state=None)

    logger.info("Appointment %s cancelled.", appointment_id)
    return {
        "cancelled": {
            "id": appt.id,
            "status": appt.status.value,
        },
        "message": "Appointment has been cancelled and the time slot is now available.",
    }


async def get_consecutive_slots(
    target_date: str,
    count: int = 2,
    *,
    db: Session,
) -> dict:
    """Find groups of consecutive back-to-back slots on a given date.

    Useful for family bookings or longer procedures that need multiple
    adjacent time slots.  Pydantic validates count is 2-5 at schema level.
    """
    try:
        target = date.fromisoformat(target_date)
    except (ValueError, TypeError) as exc:
        return {"error": f"Invalid date format. Use YYYY-MM-DD. ({exc})"}

    slot_count = count

    groups = SlotRepository.get_consecutive(db, target, slot_count)

    if not groups:
        return {
            "groups": [],
            "message": f"No groups of {slot_count} consecutive slots found on {_fmt_date(target)}.",
        }

    formatted_groups = []
    for group in groups[:3]:  # show max 3 groups
        formatted_groups.append({
            "slots": [_slot_to_dict(s) for s in group],
            "block_start": _fmt_time(group[0].start_time),
            "block_end": _fmt_time(group[-1].end_time),
            "provider": group[0].provider_name,
        })

    return {
        "groups": formatted_groups,
        "total_groups": len(groups),
        "message": f"Found {len(groups)} block(s) of {slot_count} consecutive slots on {_fmt_date(target)}.",
    }


async def get_patient_appointments(
    patient_id: str,
    *,
    db: Session,
) -> dict:
    """Return a patient's upcoming (scheduled) appointments with slot details."""
    appointments = AppointmentRepository.get_patient_appointments(
        db, patient_id, status=AppointmentStatus.scheduled
    )

    if not appointments:
        logger.info("No upcoming appointments for patient %s.", patient_id)
        return {"appointments": [], "message": "No upcoming appointments found."}

    appt_list = [_appointment_to_dict(a) for a in appointments]

    logger.info(
        "Found %d upcoming appointment(s) for patient %s.", len(appt_list), patient_id
    )
    return {"appointments": appt_list, "total": len(appt_list)}
