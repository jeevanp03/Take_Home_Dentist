"""Pydantic request/response schemas for all agent tools.

These validate at tool *execution* time (inside ``execute_tool``).
Gemini function declarations are derived from these but live in the
tool registry (``src/agent/tools/__init__.py``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    phone: str | None = Field(None, description="Phone number for verification")
    date_of_birth: str | None = Field(None, description="Date of birth (YYYY-MM-DD) for verification")


class CreatePatientInput(BaseModel):
    full_name: str = Field(..., description="Patient full name")
    phone: str = Field(..., description="Phone number (unique)")
    date_of_birth: str | None = Field(None, description="Date of birth (YYYY-MM-DD)")
    insurance_name: str | None = Field(None, description="Insurance provider name, or null for self-pay")


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
