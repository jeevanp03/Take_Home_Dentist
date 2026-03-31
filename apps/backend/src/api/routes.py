"""Core API routes — patient identification and slot queries.

The chat endpoint (``POST /api/chat``) is Phase 3C (SSE streaming).
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.auth import TokenData, verify_token
from src.cache.session import get_session, update_session
from src.db.database import get_db
from src.db.models import AppointmentStatus
from src.agent.tools.appointments import _fmt_date, _fmt_time
from src.db.repositories import (
    AppointmentRepository,
    PatientRepository,
)
from src.schemas import normalize_phone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["core"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class IdentifyRequest(BaseModel):
    mode: Literal["returning", "new", "question"]
    name: str | None = Field(None, description="Patient full name")
    phone: str | None = Field(None, description="Patient phone number")


class IdentifyResponse(BaseModel):
    status: str
    patient_id: str | None = None
    patient_name: str | None = None
    upcoming_appointments: list[dict] = []
    needs_info: list[str] = []
    message: str | None = None


# ---------------------------------------------------------------------------
# POST /api/identify
# ---------------------------------------------------------------------------

@router.post("/identify", response_model=IdentifyResponse)
async def identify_patient(
    body: IdentifyRequest,
    token: TokenData = Depends(verify_token),
    db: Session = Depends(get_db),
) -> IdentifyResponse:
    """Handle pre-agent patient identification.

    Three modes:

    - **returning**: Lookup by name + phone → load patient context →
      inject into Redis session → return appointments
    - **new**: Check for existing (prevent duplicates) → create if not
      found → inject patient_id into session → agent collects DOB/insurance
    - **question**: No lookup, just initialize session → agent handles
    """
    session_id = token.session_id

    # ------------------------------------------------------------------
    # QUESTION mode — no patient identification needed
    # ------------------------------------------------------------------
    if body.mode == "question":
        await update_session(session_id, intent="question")
        return IdentifyResponse(
            status="ok",
            message="Session ready. Ask your question!",
        )

    # ------------------------------------------------------------------
    # Validate name + phone for returning/new modes
    # ------------------------------------------------------------------
    if not body.name or not body.phone:
        return IdentifyResponse(
            status="error",
            message="Name and phone are required for patient identification.",
        )

    # Normalize phone
    try:
        phone = normalize_phone(body.phone)
    except ValueError as exc:
        return IdentifyResponse(status="error", message=str(exc))

    # ------------------------------------------------------------------
    # RETURNING mode — lookup existing patient
    # ------------------------------------------------------------------
    if body.mode == "returning":
        patient = PatientRepository.find_by_name_and_phone(db, body.name, phone)

        if patient is None:
            return IdentifyResponse(
                status="not_found",
                message="We couldn't find your record. Would you like to register as a new patient?",
            )

        # Load upcoming appointments
        appointments = AppointmentRepository.get_patient_appointments(
            db, patient.id, status=AppointmentStatus.scheduled,
        )
        appt_list = []
        for appt in appointments:
            slot = appt.slot
            if slot:
                appt_list.append({
                    "id": appt.id,
                    "type": appt.appointment_type.value,
                    "date": _fmt_date(slot.date),
                    "time": _fmt_time(slot.start_time),
                    "provider": slot.provider_name,
                })

        # Build patient context for the system prompt
        appt_summaries = [
            f"{a['type'].replace('_', ' ').title()} on {a['date']} at {a['time']}"
            for a in appt_list
        ]

        # Determine what info is still needed
        needs_info = []
        if patient.date_of_birth is None:
            needs_info.append("dob")
        if patient.insurance_name is None:
            needs_info.append("insurance")

        # Inject into Redis session
        await update_session(
            session_id,
            intent="returning",
            patient_id=patient.id,
            patient_name=patient.full_name,
            patient_context={
                "appointments": appt_summaries,
            },
        )

        logger.info("Returning patient identified: %s", patient.id)

        return IdentifyResponse(
            status="ok",
            patient_id=patient.id,
            patient_name=patient.full_name,
            upcoming_appointments=appt_list,
            needs_info=needs_info,
        )

    # ------------------------------------------------------------------
    # NEW mode — check for duplicate, then create
    # ------------------------------------------------------------------
    # Check if patient already exists (prevent duplicates)
    existing = PatientRepository.find_by_phone(db, phone)
    if existing:
        # Patient exists by phone — treat as returning
        await update_session(
            session_id,
            intent="returning",
            patient_id=existing.id,
            patient_name=existing.full_name,
        )
        return IdentifyResponse(
            status="existing",
            patient_id=existing.id,
            patient_name=existing.full_name,
            message="It looks like you already have an account! Welcome back.",
        )

    # Create new patient (name + phone only — agent collects DOB/insurance)
    result = PatientRepository.create(
        db, full_name=body.name, phone=phone,
    )

    if isinstance(result, dict):
        # IntegrityError — race condition on phone uniqueness
        return IdentifyResponse(
            status="error",
            message="Could not create your record. Please try again.",
        )

    await update_session(
        session_id,
        intent="new",
        patient_id=result.id,
        patient_name=result.full_name,
    )

    logger.info("New patient created: %s", result.id)

    return IdentifyResponse(
        status="ok",
        patient_id=result.id,
        patient_name=result.full_name,
        needs_info=["dob", "insurance"],
        message="Welcome! Mia will help you get set up.",
    )
