"""SQLAlchemy 2.0 models for the dental practice chatbot."""

from __future__ import annotations

import enum
from datetime import date, datetime, time
from uuid import uuid4

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    Boolean,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id() -> str:
    """Generate a 16-character hex ID from a UUID4."""
    return uuid4().hex[:16]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AppointmentType(str, enum.Enum):
    cleaning = "cleaning"
    general_checkup = "general_checkup"
    emergency = "emergency"
    consultation = "consultation"
    follow_up = "follow_up"


class AppointmentStatus(str, enum.Enum):
    scheduled = "scheduled"
    cancelled = "cancelled"
    completed = "completed"
    no_show = "no_show"


# ---------------------------------------------------------------------------
# Declarative Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------------

class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(
        String(16), primary_key=True, default=_new_id
    )
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    insurance_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    appointments: Mapped[list[Appointment]] = relationship(
        back_populates="patient", cascade="save-update, merge"
    )
    conversation_logs: Mapped[list[ConversationLog]] = relationship(
        back_populates="patient"
    )

    def __repr__(self) -> str:
        return f"<Patient {self.full_name!r} phone={self.phone!r}>"


# ---------------------------------------------------------------------------
# TimeSlot
# ---------------------------------------------------------------------------

class TimeSlot(Base):
    __tablename__ = "time_slots"
    __table_args__ = (
        Index("ix_timeslots_date_available", "date", "is_available"),
    )

    id: Mapped[str] = mapped_column(
        String(16), primary_key=True, default=_new_id
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Relationships
    appointment: Mapped[Appointment | None] = relationship(
        back_populates="slot", uselist=False
    )

    def __repr__(self) -> str:
        return (
            f"<TimeSlot {self.date} {self.start_time}-{self.end_time} "
            f"available={self.is_available}>"
        )


# ---------------------------------------------------------------------------
# Appointment
# ---------------------------------------------------------------------------

class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_patient_id", "patient_id"),
        Index("ix_appointments_status", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(16), primary_key=True, default=_new_id
    )
    patient_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("patients.id"), nullable=False
    )
    slot_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("time_slots.id"), nullable=False
    )
    appointment_type: Mapped[AppointmentType] = mapped_column(
        Enum(AppointmentType), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus), default=AppointmentStatus.scheduled
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    patient: Mapped[Patient] = relationship(back_populates="appointments")
    slot: Mapped[TimeSlot] = relationship(back_populates="appointment")

    def __repr__(self) -> str:
        return (
            f"<Appointment {self.id} patient={self.patient_id} "
            f"type={self.appointment_type.value} status={self.status.value}>"
        )


# ---------------------------------------------------------------------------
# ConversationLog
# ---------------------------------------------------------------------------

class ConversationLog(Base):
    __tablename__ = "conversation_logs"

    id: Mapped[str] = mapped_column(
        String(16), primary_key=True, default=_new_id
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    patient_id: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("patients.id"), nullable=True
    )
    messages: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    patient: Mapped[Patient | None] = relationship(back_populates="conversation_logs")

    def __repr__(self) -> str:
        return f"<ConversationLog session={self.session_id!r}>"
