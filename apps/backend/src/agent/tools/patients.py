"""Patient lookup, creation, and update tools for the ReAct agent."""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from src.cache.session import update_session
from src.db.models import Patient
from src.db.repositories import PatientRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dob(dob: str | None) -> date | None:
    """Parse a YYYY-MM-DD string into a ``date``, returning *None* on failure."""
    if not dob:
        return None
    try:
        return datetime.strptime(dob.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        logger.warning("Could not parse date_of_birth: %r", dob)
        return None


def _patient_to_dict(patient: Patient) -> dict:
    """Convert a Patient ORM instance to a plain, JSON-serializable dict."""
    return {
        "id": patient.id,
        "full_name": patient.full_name,
        "phone": patient.phone,
        "date_of_birth": patient.date_of_birth.isoformat() if patient.date_of_birth else None,
        "insurance_name": patient.insurance_name,
    }


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

async def lookup_patient(
    name: str,
    phone: str | None = None,
    date_of_birth: str | None = None,
    *,
    db: Session,
    session_id: str,
) -> dict:
    """Look up an existing patient by name + phone **or** name + date of birth.

    On success the patient's ID is written into the Redis session so
    downstream tools (booking, etc.) can reference it.
    """
    patient: Patient | None = None

    if phone:
        patient = PatientRepository.find_by_name_and_phone(db, name, phone)
    elif date_of_birth:
        parsed_dob = _parse_dob(date_of_birth)
        if parsed_dob is None:
            return {"error": f"Invalid date format: '{date_of_birth}'. Expected YYYY-MM-DD."}
        patient = PatientRepository.find_by_name_and_dob(db, name, parsed_dob)
    else:
        return {"error": "Either phone or date_of_birth must be provided to look up a patient."}

    if patient is None:
        logger.info("Patient not found: name=%r phone=%r dob=%r", name, phone, date_of_birth)
        return {
            "error": "Patient not found. Please verify the information or create a new patient record."
        }

    # Persist patient_id on the session for downstream tools
    await update_session(session_id, patient_id=patient.id)
    logger.info("Looked up patient %s (%s)", patient.id, patient.full_name)
    return _patient_to_dict(patient)


async def create_patient(
    full_name: str,
    phone: str,
    date_of_birth: str | None = None,
    insurance_name: str | None = None,
    *,
    db: Session,
    session_id: str,
) -> dict:
    """Create a new patient record.

    Returns the newly created patient dict on success, or an error dict
    if the write fails (e.g. duplicate phone number).
    """
    parsed_dob = _parse_dob(date_of_birth)

    result = PatientRepository.create(
        db,
        full_name=full_name,
        phone=phone,
        date_of_birth=parsed_dob,
        insurance_name=insurance_name,
    )

    # PatientRepository.create returns a dict on IntegrityError
    if isinstance(result, dict):
        logger.warning("Failed to create patient %r: %s", full_name, result)
        return result

    # Success — update session with the new patient_id
    await update_session(session_id, patient_id=result.id)
    logger.info("Created patient %s (%s)", result.id, full_name)
    return _patient_to_dict(result)


async def update_patient(
    patient_id: str,
    date_of_birth: str | None = None,
    insurance_name: str | None = None,
    *,
    db: Session,
) -> dict:
    """Update mutable fields on an existing patient record.

    Only non-None fields are applied; omitted fields are left unchanged.
    """
    patient = PatientRepository.find_by_id(db, patient_id)
    if patient is None:
        logger.warning("update_patient called with unknown id: %s", patient_id)
        return {"error": f"Patient {patient_id} not found."}

    if date_of_birth is not None:
        parsed_dob = _parse_dob(date_of_birth)
        if parsed_dob is None:
            return {"error": f"Invalid date format: '{date_of_birth}'. Expected YYYY-MM-DD."}
        patient.date_of_birth = parsed_dob

    if insurance_name is not None:
        patient.insurance_name = insurance_name

    try:
        db.commit()
        db.refresh(patient)
    except Exception:
        db.rollback()
        logger.exception("Failed to update patient %s", patient_id)
        return {"error": "Failed to save patient update. Please try again."}

    logger.info("Updated patient %s", patient_id)
    return _patient_to_dict(patient)
