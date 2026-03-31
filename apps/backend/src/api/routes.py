"""Core API routes — chat (SSE), patient identification, slot queries.

All PHI-touching endpoints require a valid JWT Bearer token.
"""

from __future__ import annotations

import json
import logging
import time as _time
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.agent.orchestrator import run as orchestrator_run
from src.agent.tools.appointments import _fmt_date, _fmt_time
from src.api.auth import TokenData, verify_token
from src.api.debounce import debounce_message
from src.cache.session import append_message, get_session, update_session
from src.db.database import get_db
from src.db.models import AppointmentStatus
from src.db.repositories import (
    AppointmentRepository,
    PatientRepository,
    SlotRepository,
)
from src.schemas import normalize_phone

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["core"])

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
_RATE_LIMIT = 10          # messages per window
_RATE_WINDOW = 60         # seconds
_rate_counters: dict[str, list[float]] = {}  # session_id → [timestamps]


_last_rate_cleanup: float = 0.0


def _check_rate_limit(session_id: str) -> bool:
    """Return True if the session is within the rate limit, False if exceeded."""
    global _last_rate_cleanup  # noqa: PLW0603
    now = _time.time()

    # Periodic cleanup of stale sessions (every 60s)
    if now - _last_rate_cleanup > _RATE_WINDOW:
        _last_rate_cleanup = now
        stale = [k for k, v in _rate_counters.items() if not v or now - v[-1] > _RATE_WINDOW]
        for k in stale:
            del _rate_counters[k]

    timestamps = _rate_counters.get(session_id, [])
    timestamps = [t for t in timestamps if now - t < _RATE_WINDOW]
    if len(timestamps) >= _RATE_LIMIT:
        _rate_counters[session_id] = timestamps
        return False
    timestamps.append(now)
    _rate_counters[session_id] = timestamps
    return True


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="User message")


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
# POST /api/chat — SSE streaming
# ---------------------------------------------------------------------------

async def _sse_generator(session_id: str, message: str):
    """Async generator that yields SSE-formatted events from the orchestrator.

    Creates its own DB session to avoid FastAPI closing the Depends(get_db)
    session before the stream is fully consumed.
    """
    from src.db.database import SessionLocal

    db = SessionLocal()
    try:
        async for chunk in orchestrator_run(session_id, message, db):
            chunk_type = chunk.get("type", "text")
            content = chunk.get("content", "")

            if chunk_type == "end":
                yield "data: [DONE]\n\n"
                return

            payload = json.dumps({"type": chunk_type, "content": content})
            yield f"data: {payload}\n\n"

    except Exception as exc:
        logger.exception("SSE stream error for session %s: %s", session_id, exc)
        error_payload = json.dumps({
            "type": "error",
            "content": "I'm having some trouble right now. You can reach us at (555) 123-4567.",
        })
        yield f"data: {error_payload}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        db.close()


@router.post("/chat")
async def chat(
    body: ChatRequest,
    token: TokenData = Depends(verify_token),
):
    """Send a message to the dental assistant and receive an SSE stream.

    The response is a ``text/event-stream`` with events formatted as::

        data: {"type": "text", "content": "Hello! I'm Mia..."}

        data: {"type": "text", "content": "Let me check that for you."}

        data: [DONE]
    """
    session_id = token.session_id

    # --- Rate limiting ---
    if not _check_rate_limit(session_id):
        async def rate_limit_stream():
            payload = json.dumps({
                "type": "text",
                "content": "You're sending messages faster than I can keep up — give me a moment!",
            })
            yield f"data: {payload}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            rate_limit_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- Debounce ---
    debounced = await debounce_message(session_id, body.message)
    if debounced is None:
        # Message was buffered — the first caller's stream will include it
        async def buffered_stream():
            payload = json.dumps({
                "type": "text",
                "content": "",  # empty — frontend ignores empty text chunks
            })
            yield f"data: {payload}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            buffered_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return StreamingResponse(
        _sse_generator(session_id, debounced),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/slots
# ---------------------------------------------------------------------------

@router.get("/slots")
async def get_slots(
    token: TokenData = Depends(verify_token),
    db: Session = Depends(get_db),
    date_start: str | None = Query(None, description="Start date YYYY-MM-DD (defaults to today)"),
    date_end: str | None = Query(None, description="End date YYYY-MM-DD (defaults to 2 weeks out)"),
    provider: str | None = Query(None, description="Filter by provider name"),
):
    """Return available appointment slots as JSON."""
    from datetime import timedelta

    try:
        start = date.fromisoformat(date_start) if date_start else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date_start format: {date_start}. Use YYYY-MM-DD.")

    try:
        end = date.fromisoformat(date_end) if date_end else start + timedelta(days=14)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date_end format: {date_end}. Use YYYY-MM-DD.")

    slots = SlotRepository.get_available(db, start, end, provider_name=provider)

    return {
        "slots": [
            {
                "id": s.id,
                "date": _fmt_date(s.date),
                "date_iso": s.date.isoformat(),
                "start_time": _fmt_time(s.start_time),
                "end_time": _fmt_time(s.end_time),
                "provider_name": s.provider_name,
            }
            for s in slots
        ],
        "total": len(slots),
    }


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
    # QUESTION mode
    # ------------------------------------------------------------------
    if body.mode == "question":
        await update_session(session_id, intent="question")
        return IdentifyResponse(
            status="ok",
            message="Session ready. Ask your question!",
        )

    # ------------------------------------------------------------------
    # Validate name + phone
    # ------------------------------------------------------------------
    if not body.name or not body.phone:
        return IdentifyResponse(
            status="error",
            message="Name and phone are required for patient identification.",
        )

    try:
        phone = normalize_phone(body.phone)
    except ValueError as exc:
        return IdentifyResponse(status="error", message=str(exc))

    # ------------------------------------------------------------------
    # RETURNING mode
    # ------------------------------------------------------------------
    if body.mode == "returning":
        patient = PatientRepository.find_by_name_and_phone(db, body.name, phone)

        if patient is None:
            return IdentifyResponse(
                status="not_found",
                message="We couldn't find your record. Would you like to register as a new patient?",
            )

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

        appt_summaries = [
            f"{a['type'].replace('_', ' ').title()} on {a['date']} at {a['time']}"
            for a in appt_list
        ]

        needs_info = []
        if patient.date_of_birth is None:
            needs_info.append("dob")
        if patient.insurance_name is None:
            needs_info.append("insurance")

        await update_session(
            session_id,
            intent="returning",
            patient_id=patient.id,
            patient_name=patient.full_name,
            patient_context={"appointments": appt_summaries},
        )

        # Seed the greeting into session history so the agent knows what
        # was already displayed to the patient (greeting is built client-side).
        greeting = f"Welcome back, {patient.full_name}!"
        if appt_summaries:
            greeting += " You have upcoming appointments: " + "; ".join(appt_summaries) + "."
        greeting += " How can I help you today?"
        await append_message(session_id, {"role": "assistant", "content": greeting})

        logger.info("Returning patient identified: %s", patient.id)

        return IdentifyResponse(
            status="ok",
            patient_id=patient.id,
            patient_name=patient.full_name,
            upcoming_appointments=appt_list,
            needs_info=needs_info,
        )

    # ------------------------------------------------------------------
    # NEW mode
    # ------------------------------------------------------------------
    existing = PatientRepository.find_by_phone(db, phone)
    if existing:
        await update_session(
            session_id,
            intent="returning",
            patient_id=existing.id,
            patient_name=existing.full_name,
        )
        await append_message(session_id, {
            "role": "assistant",
            "content": f"Welcome back, {existing.full_name}! It looks like you already have an account. How can I help you today?",
        })
        return IdentifyResponse(
            status="existing",
            patient_id=existing.id,
            patient_name=existing.full_name,
            message="It looks like you already have an account! Welcome back.",
        )

    result = PatientRepository.create(db, full_name=body.name, phone=phone)

    if isinstance(result, dict):
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

    # Seed the greeting into session history
    new_greeting = (
        f"Welcome to Bright Smile Dental, {result.full_name}! "
        "I'm Mia, and I'll help you get set up. "
        "I just need a couple more details — what's your date of birth?"
    )
    await append_message(session_id, {"role": "assistant", "content": new_greeting})

    logger.info("New patient created: %s", result.id)

    return IdentifyResponse(
        status="ok",
        patient_id=result.id,
        patient_name=result.full_name,
        needs_info=["dob", "insurance"],
        message="Welcome! Mia will help you get set up.",
    )


# ---------------------------------------------------------------------------
# POST /api/feedback
# ---------------------------------------------------------------------------

class FeedbackRequest(BaseModel):
    message_id: str = Field(..., description="Frontend message ID")
    feedback: Literal["up", "down"] = Field(..., description="Thumbs up or down")
    session_id: str | None = Field(None, description="Session ID for context")


@router.post("/feedback")
async def submit_feedback(
    body: FeedbackRequest,
    token: TokenData = Depends(verify_token),
):
    """Record user feedback on an assistant message.

    Stored as a log entry for now — can be expanded to a DB table later.
    """
    logger.info(
        "Feedback received: session=%s message=%s feedback=%s",
        token.session_id,
        body.message_id,
        body.feedback,
    )
    return {"status": "ok"}
