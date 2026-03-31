"""Seed the database with sample time-slots, patients, and appointments.

Usage:
    cd apps/backend
    python -m scripts.seed

Idempotent — checks for existing data before inserting.
"""

from __future__ import annotations

import random
import sys
from datetime import date, datetime, time, timedelta

from src.db.database import SessionLocal, init_db
from src.db.models import (
    Appointment,
    AppointmentStatus,
    AppointmentType,
    Patient,
    TimeSlot,
)

PROVIDER = "Dr. Sarah Smith"
SLOT_DURATION_MIN = 30
DAY_START = time(8, 0)
DAY_END = time(17, 30)   # last slot starts at 17:00
UNAVAILABLE_PCT = 0.15
SEED_RNG = random.Random(42)  # deterministic for reproducibility


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _time_range(start: time, end: time, step_min: int):
    """Yield (start_time, end_time) tuples covering [start, end)."""
    cursor = datetime.combine(date.today(), start)
    limit = datetime.combine(date.today(), end)
    delta = timedelta(minutes=step_min)
    while cursor < limit:
        next_cursor = cursor + delta
        yield cursor.time(), next_cursor.time()
        cursor = next_cursor


def _weekdays(start: date, num_days: int):
    """Yield Mon-Sat dates for *num_days* calendar days starting from *start*."""
    d = start
    end = start + timedelta(days=num_days)
    while d < end:
        if d.weekday() < 6:  # Mon=0 … Sat=5
            yield d
        d += timedelta(days=1)


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------

def seed_slots(db) -> list[TimeSlot]:
    """Create 2 weeks of 30-min slots, Mon-Sat, 08:00-17:30."""
    existing = db.query(TimeSlot).first()
    if existing:
        print("  Slots already seeded — skipping.")
        return list(db.query(TimeSlot).all())

    today = date.today()
    slots: list[TimeSlot] = []

    for d in _weekdays(today, 14):
        for st, et in _time_range(DAY_START, DAY_END, SLOT_DURATION_MIN):
            available = SEED_RNG.random() > UNAVAILABLE_PCT
            slot = TimeSlot(
                date=d,
                start_time=st,
                end_time=et,
                is_available=available,
                provider_name=PROVIDER,
            )
            slots.append(slot)

    db.add_all(slots)
    db.commit()
    for s in slots:
        db.refresh(s)
    print(f"  Created {len(slots)} time slots.")
    return slots


SEED_PATIENTS = [
    {
        "full_name": "Alice Johnson",
        "phone": "555-0101",
        "date_of_birth": date(1990, 3, 15),
        "insurance_name": "Delta Dental",
    },
    {
        "full_name": "Bob Martinez",
        "phone": "555-0102",
        "date_of_birth": date(1985, 7, 22),
        "insurance_name": "Cigna Dental",
    },
    {
        "full_name": "Carol Williams",
        "phone": "555-0103",
        "date_of_birth": date(1978, 11, 3),
        "insurance_name": None,
    },
    {
        "full_name": "David Chen",
        "phone": "555-0104",
        "date_of_birth": date(2001, 1, 8),
        "insurance_name": "MetLife Dental",
    },
    {
        "full_name": "Eve Okafor",
        "phone": "555-0105",
        "date_of_birth": date(1995, 6, 30),
        "insurance_name": "Aetna Dental",
    },
]


def seed_patients(db) -> list[Patient]:
    existing = db.query(Patient).first()
    if existing:
        print("  Patients already seeded — skipping.")
        return list(db.query(Patient).all())

    patients: list[Patient] = []
    for data in SEED_PATIENTS:
        p = Patient(**data)
        patients.append(p)

    db.add_all(patients)
    db.commit()
    for p in patients:
        db.refresh(p)
    print(f"  Created {len(patients)} patients.")
    return patients


SEED_APPOINTMENTS = [
    # (patient index, appointment_type, notes)
    (0, AppointmentType.cleaning, "Regular 6-month cleaning"),
    (1, AppointmentType.general_checkup, "Annual checkup"),
    (2, AppointmentType.consultation, "New patient consultation"),
]


def seed_appointments(db, patients: list[Patient], slots: list[TimeSlot]) -> None:
    existing = db.query(Appointment).first()
    if existing:
        print("  Appointments already seeded — skipping.")
        return

    # Pick available slots for the appointments
    available = [s for s in slots if s.is_available]
    if len(available) < len(SEED_APPOINTMENTS):
        print("  Not enough available slots to create seed appointments.")
        return

    chosen_slots = SEED_RNG.sample(available, len(SEED_APPOINTMENTS))

    for (p_idx, appt_type, notes), slot in zip(SEED_APPOINTMENTS, chosen_slots):
        appt = Appointment(
            patient_id=patients[p_idx].id,
            slot_id=slot.id,
            appointment_type=appt_type,
            notes=notes,
            status=AppointmentStatus.scheduled,
        )
        slot.is_available = False
        db.add(appt)

    db.commit()
    print(f"  Created {len(SEED_APPOINTMENTS)} appointments.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Initializing database...")
    init_db()

    db = SessionLocal()
    try:
        print("Seeding time slots...")
        slots = seed_slots(db)

        print("Seeding patients...")
        patients = seed_patients(db)

        print("Seeding appointments...")
        seed_appointments(db, patients, slots)

        # Quick verification
        slot_count = db.query(TimeSlot).count()
        patient_count = db.query(Patient).count()
        appt_count = db.query(Appointment).count()
        avail_count = db.query(TimeSlot).filter(TimeSlot.is_available.is_(True)).count()

        print("\n--- Seed Summary ---")
        print(f"  Time slots : {slot_count}  ({avail_count} available)")
        print(f"  Patients   : {patient_count}")
        print(f"  Appointments: {appt_count}")
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
