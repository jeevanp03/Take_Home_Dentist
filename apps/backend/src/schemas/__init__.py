"""Pydantic request/response schemas for all agent tools.

These validate at tool *execution* time (inside ``execute_tool``).
Gemini function declarations are derived from these but live in the
tool registry (``src/agent/tools/__init__.py``).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Phone normalization helper
# ---------------------------------------------------------------------------

_NON_DIGIT = re.compile(r"\D")


def normalize_phone(raw: str) -> str:
    """Strip non-digit characters and validate length.

    Accepts common US formats: 555-123-4567, (555) 123-4567, 555.123.4567,
    5551234567.  Returns digits-only string.
    """
    digits = _NON_DIGIT.sub("", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]  # strip leading country code
    if len(digits) != 10:
        raise ValueError(f"Phone must be 10 digits, got {len(digits)} from '{raw}'")
    return digits


# ---------------------------------------------------------------------------
# Knowledge tools
# ---------------------------------------------------------------------------

class SearchKnowledgeBaseInput(BaseModel):
    query: str = Field(..., description="Natural-language search query")


class SearchPastConversationsInput(BaseModel):
    patient_id: str = Field(..., description="Patient ID to filter by")
    query: str = Field(..., description="What to search for in past conversations")


# ---------------------------------------------------------------------------
# Patient tools
# ---------------------------------------------------------------------------

class LookupPatientInput(BaseModel):
    name: str = Field(..., description="Patient full name (partial match)")
    phone: str | None = Field(None, description="Phone number for verification (digits only)")
    date_of_birth: str | None = Field(None, description="Date of birth (YYYY-MM-DD) for verification")

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_phone(v)


class CreatePatientInput(BaseModel):
    full_name: str = Field(..., description="Patient full name")
    phone: str = Field(..., description="Phone number (unique, digits only)")
    date_of_birth: str | None = Field(None, description="Date of birth (YYYY-MM-DD)")
    insurance_name: str | None = Field(None, description="Insurance provider name, or null for self-pay")

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        return normalize_phone(v)


class UpdatePatientInput(BaseModel):
    patient_id: str = Field(..., description="Patient ID to update")
    date_of_birth: str | None = Field(None, description="Date of birth (YYYY-MM-DD)")
    insurance_name: str | None = Field(None, description="Insurance provider name")

    @model_validator(mode="after")
    def at_least_one_field(self) -> UpdatePatientInput:
        if self.date_of_birth is None and self.insurance_name is None:
            raise ValueError("At least one of date_of_birth or insurance_name must be provided.")
        return self


# ---------------------------------------------------------------------------
# Appointment tools
# ---------------------------------------------------------------------------

class GetAvailableSlotsInput(BaseModel):
    date_start: str = Field(..., description="Start date (YYYY-MM-DD)")
    date_end: str = Field(..., description="End date (YYYY-MM-DD)")
    time_preference: Literal["morning", "afternoon", "any"] = Field(
        "any", description="Time of day preference"
    )
    provider_name: str | None = Field(
        None, description="Filter by provider name (e.g. 'Dr. Sarah Smith')"
    )


class GetConsecutiveSlotsInput(BaseModel):
    target_date: str = Field(..., description="Date to search (YYYY-MM-DD)")
    count: int = Field(2, ge=2, le=5, description="Number of consecutive back-to-back slots needed (2-5)")


class BookAppointmentInput(BaseModel):
    patient_id: str = Field(..., description="Patient ID")
    slot_id: str = Field(..., description="Time slot ID to book")
    appointment_type: Literal[
        "cleaning", "general_checkup", "emergency", "consultation", "follow_up"
    ] = Field(..., description="Type of appointment")
    notes: str | None = Field(None, description="Optional notes (e.g. emergency details)")


class RescheduleAppointmentInput(BaseModel):
    appointment_id: str = Field(..., description="Existing appointment ID")
    new_slot_id: str = Field(..., description="New time slot ID")


class CancelAppointmentInput(BaseModel):
    appointment_id: str = Field(..., description="Appointment ID to cancel")


class GetPatientAppointmentsInput(BaseModel):
    patient_id: str = Field(..., description="Patient ID")


# ---------------------------------------------------------------------------
# Notification tool
# ---------------------------------------------------------------------------

class NotifyStaffInput(BaseModel):
    type: Literal["emergency", "special_request", "escalation"] = Field(
        ..., description="Notification type"
    )
    message: str = Field(..., description="Details for the staff")
    patient_id: str | None = Field(None, description="Patient ID if known")


# ---------------------------------------------------------------------------
# Practice info tool
# ---------------------------------------------------------------------------

class GetPracticeInfoInput(BaseModel):
    """No parameters — returns static practice information."""
    pass
