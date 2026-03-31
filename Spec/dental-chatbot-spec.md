# Dental Practice Chatbot — Technical Spec & Implementation Plan

---

## 1. Architecture Overview

### High-Level System Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    FRONTEND (Next.js 15)                     │
│   Chat UI  ·  Typing indicators  ·  Message history          │
│   Port 3000                                                  │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP POST + SSE (streaming)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│            BACKEND (FastAPI + Uvicorn, Python 3.11+)         │
│   POST /api/chat  ·  GET /api/slots  ·  CORS middleware      │
│   Port 8000                                                  │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              AGENTIC ORCHESTRATOR (ReAct loop)               │
│                                                              │
│  System Prompt + Conversation Context (from Redis)           │
│         │                                                    │
│         ├── Tool: search_knowledge_base  ──► ChromaDB        │
│         │   (practice info + PubMed + MedlinePlus)  (coll 1) │
│         │                                                    │
│         ├── Tool: search_past_conversations ► ChromaDB       │
│         │         (patient history RAG)       (Collection 2) │
│         │                                                    │
│         ├── Tool: lookup_patient ───────────► SQLAlchemy     │
│         ├── Tool: create_patient ───────────► SQLAlchemy     │
│         ├── Tool: get_available_slots ──────► SQLAlchemy     │
│         ├── Tool: book_appointment ─────────► SQLAlchemy     │
│         ├── Tool: reschedule_appointment ──► SQLAlchemy      │
│         ├── Tool: cancel_appointment ──────► SQLAlchemy      │
│         ├── Tool: notify_staff ────────────► (log/webhook)   │
│         └── Tool: get_practice_info ───────► (static)        │
│                                                              │
└──────────────────────────┬───────────────────────────────────┘
                           │
           ┌───────────────┼───────────────────┐
           ▼               ▼                   ▼
┌───────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ SQLite (dev)  │ │ChromaDB embedded│ │     Redis       │
│ Postgres(prod)│ │ PersistentClient│ │  (Hot State)    │
│  SQLAlchemy   │ │  (in-process)   │ │                 │
│               │ │                 │ │ session:{id}    │
│ patients      │ │ dental_kb       │ │  - messages[]   │
│ appointments  │ │  (practice +    │ │  - collected     │
│ time_slots    │ │   PubMed +      │ │    fields{}     │
│ conv_logs     │ │   MedlinePlus)  │ │  - intent       │
│               │ │                 │ │  - booking_state │
│               │ │ conversations   │ │                 │
│               │ │  (past chats)   │ │                 │
└───────────────┘ └─────────────────┘ └─────────────────┘
```

### Why This Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| **Frontend** | Next.js 15 (App Router) | Fast to scaffold a chat UI. Talks to Python backend over HTTP. Framework-agnostic — backend could serve mobile, SMS, or phone system. |
| **Backend** | FastAPI + Uvicorn | Async-native Python. SSE streaming via `StreamingResponse`. Pydantic validation on every request. Uvicorn is the standard ASGI server. |
| **ORM** | SQLAlchemy 2.0 | Industry standard. Declarative models, session-based transactions, dialect-agnostic — switching SQLite to Postgres is a connection string swap. |
| **Relational DB** | SQLite (dev) / Postgres (prod) | SQLite for the assessment: zero setup, single file, ships in repo. SQLAlchemy abstracts the dialect. |
| **Vector DB** | ChromaDB (`PersistentClient`, embedded) | Python-native, runs in-process with FastAPI — no separate server, no Docker container, no network hop. Two collections: `dental_kb` (knowledge) and `conversations` (past chats). Data persists to `./data/chroma/`. |
| **Knowledge Sources** | PubMed E-utilities + MedlinePlus Web Service + practice markdown | Real dental knowledge from authoritative government APIs. PubMed for research abstracts, MedlinePlus for patient-facing summaries, practice docs for office-specific info. All free, no auth required. |
| **In-Memory Cache** | Redis (`redis-py` async) | Hot conversation state: message history, collected fields, intent. Sub-ms reads during the ReAct loop. TTL-based expiry for abandoned sessions. |
| **LLM** | Gemini 2.0 Flash (`google-generativeai`) | Free tier, fast, solid function calling. Python SDK is first-class. |

### Key Architectural Decisions

**Separate frontend and backend.** Next.js handles only the UI; FastAPI handles all business logic, agent orchestration, and data access. They communicate over HTTP + SSE. Either can be swapped independently.

**SQLAlchemy 2.0 style (not legacy 1.x).** Uses `mapped_column()`, `select()`, and `Session.execute()`. Models double as documentation.

**ChromaDB embedded, not HTTP.** Since all access goes through the Python backend, `PersistentClient` runs in the same process as FastAPI. No Docker container for ChromaDB, no network hop. Data persists to disk at `./data/chroma/`.

**Two-tier knowledge base.** Practice-specific markdown (hours, insurance, location) is hand-authored. Clinical dental knowledge is pulled from PubMed and MedlinePlus APIs during setup. A `--refresh` flag re-pulls and re-embeds.

**Redis as conversation buffer, not source of truth.** Active conversation state lives in Redis with 30-min TTL. When a conversation ends, it's summarized and flushed to ChromaDB (conversations collection) + SQLite (conversation_logs table). If Redis dies mid-conversation, the patient restarts — no data corruption.

**Agent, not chain.** A ReAct agent with tools handles unpredictable conversation paths (patient starts booking, pivots to insurance question, comes back to booking). A rigid chain can't do this.

**JWT auth, not session cookies.** Stateless tokens carry `session_id` and optional `patient_id` as claims. No server-side session store needed (Redis is for conversation state, not auth state). The frontend gets a token on load and includes it in every request. Token refresh happens transparently at the 50-minute mark.

**Concurrency via async + semaphores, not threads.** FastAPI + Uvicorn handle thousands of concurrent connections via Python's asyncio event loop. The LLM API (the slowest bottleneck) is gated by an `asyncio.Semaphore` to avoid rate limiting. SQLAlchemy connection pooling and Redis connection pooling handle the data layer. SQLite's WAL mode allows concurrent reads during writes for the assessment; Postgres handles concurrency natively in production.

---

## 2. Data Models

### 2.1 SQLAlchemy Models

```python
# backend/src/db/models.py

import uuid
from datetime import datetime, date, time
from typing import Optional

from sqlalchemy import (
    String, Boolean, Date, Time, DateTime, Text, ForeignKey,
    Enum as SAEnum, Index
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship
)
import enum


class Base(DeclarativeBase):
    pass


class AppointmentType(str, enum.Enum):
    CLEANING = "cleaning"
    GENERAL_CHECKUP = "general_checkup"
    EMERGENCY = "emergency"


class AppointmentStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(
        String(16), primary_key=True,
        default=lambda: uuid.uuid4().hex[:16]
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    insurance_name: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True  # NULL = self-pay / no insurance
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    appointments: Mapped[list["Appointment"]] = relationship(back_populates="patient")


class TimeSlot(Base):
    __tablename__ = "time_slots"

    id: Mapped[str] = mapped_column(
        String(16), primary_key=True,
        default=lambda: uuid.uuid4().hex[:16]
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    provider_name: Mapped[str] = mapped_column(String(255), default="Dr. Smith")

    appointment: Mapped[Optional["Appointment"]] = relationship(back_populates="slot")

    __table_args__ = (
        Index("idx_slots_date_available", "date", "is_available"),
    )


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(
        String(16), primary_key=True,
        default=lambda: uuid.uuid4().hex[:16]
    )
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), nullable=False)
    slot_id: Mapped[str] = mapped_column(ForeignKey("time_slots.id"), nullable=False)
    appointment_type: Mapped[AppointmentType] = mapped_column(
        SAEnum(AppointmentType), nullable=False
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[AppointmentStatus] = mapped_column(
        SAEnum(AppointmentStatus), default=AppointmentStatus.SCHEDULED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    patient: Mapped["Patient"] = relationship(back_populates="appointments")
    slot: Mapped["TimeSlot"] = relationship(back_populates="appointment")

    __table_args__ = (
        Index("idx_appointments_patient", "patient_id"),
        Index("idx_appointments_status", "status"),
    )


class ConversationLog(Base):
    __tablename__ = "conversation_logs"

    id: Mapped[str] = mapped_column(
        String(16), primary_key=True,
        default=lambda: uuid.uuid4().hex[:16]
    )
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    patient_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("patients.id"), nullable=True
    )
    messages: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
```

### 2.2 Database Engine & Session

```python
# backend/src/db/database.py

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from src.db.models import Base
from src.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False}
    if settings.DATABASE_URL.startswith("sqlite") else {},
    echo=settings.DEBUG,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    """Create all tables. No-op if they already exist."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    """FastAPI dependency — yields a session, auto-closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### 2.3 Repository Layer

```python
# backend/src/db/repositories.py  (key methods)

from sqlalchemy import select, and_
from sqlalchemy.orm import Session
from src.db.models import Patient, TimeSlot, Appointment, AppointmentStatus


class PatientRepository:
    def __init__(self, db: Session):
        self.db = db

    def find_by_name_and_phone(self, name: str, phone: str) -> Patient | None:
        stmt = select(Patient).where(and_(
            Patient.full_name.ilike(f"%{name}%"),
            Patient.phone == phone
        ))
        return self.db.execute(stmt).scalar_one_or_none()

    def find_by_name_and_dob(self, name: str, dob: str) -> Patient | None:
        stmt = select(Patient).where(and_(
            Patient.full_name.ilike(f"%{name}%"),
            Patient.date_of_birth == dob
        ))
        return self.db.execute(stmt).scalar_one_or_none()

    def create(self, **kwargs) -> Patient:
        patient = Patient(**kwargs)
        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)
        return patient


class SlotRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_available(
        self, date_start: str, date_end: str, time_pref: str = "any"
    ) -> list[TimeSlot]:
        stmt = select(TimeSlot).where(and_(
            TimeSlot.date >= date_start,
            TimeSlot.date <= date_end,
            TimeSlot.is_available == True,
        )).order_by(TimeSlot.date, TimeSlot.start_time)

        if time_pref == "morning":
            stmt = stmt.where(TimeSlot.start_time < "12:00")
        elif time_pref == "afternoon":
            stmt = stmt.where(TimeSlot.start_time >= "12:00")

        return list(self.db.execute(stmt).scalars().all())

    def get_consecutive(self, target_date: str, count: int) -> list[list[TimeSlot]]:
        """Find groups of N back-to-back available slots (family booking)."""
        slots = self.get_available(target_date, target_date)
        groups = []
        for i in range(len(slots) - count + 1):
            window = slots[i:i + count]
            is_consecutive = all(
                window[j].end_time == window[j + 1].start_time
                for j in range(len(window) - 1)
            )
            if is_consecutive:
                groups.append(window)
        return groups


class AppointmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def book(self, patient_id: str, slot_id: str,
             appt_type: str, notes: str = None) -> Appointment | None:
        """Transactional: check availability → claim slot → create appointment."""
        slot = self.db.get(TimeSlot, slot_id)
        if not slot or not slot.is_available:
            return None
        slot.is_available = False
        appt = Appointment(
            patient_id=patient_id, slot_id=slot_id,
            appointment_type=appt_type, notes=notes,
        )
        self.db.add(appt)
        self.db.commit()
        self.db.refresh(appt)
        return appt

    def cancel(self, appointment_id: str) -> bool:
        appt = self.db.get(Appointment, appointment_id)
        if not appt:
            return False
        appt.status = AppointmentStatus.CANCELLED
        slot = self.db.get(TimeSlot, appt.slot_id)
        if slot:
            slot.is_available = True
        self.db.commit()
        return True

    def reschedule(self, appointment_id: str, new_slot_id: str) -> Appointment | None:
        """Atomic: free old slot, claim new slot."""
        appt = self.db.get(Appointment, appointment_id)
        new_slot = self.db.get(TimeSlot, new_slot_id)
        if not appt or not new_slot or not new_slot.is_available:
            return None
        old_slot = self.db.get(TimeSlot, appt.slot_id)
        if old_slot:
            old_slot.is_available = True
        new_slot.is_available = False
        appt.slot_id = new_slot_id
        self.db.commit()
        self.db.refresh(appt)
        return appt

    def get_patient_appointments(self, patient_id: str) -> list[Appointment]:
        stmt = select(Appointment).where(and_(
            Appointment.patient_id == patient_id,
            Appointment.status == AppointmentStatus.SCHEDULED,
        )).order_by(Appointment.created_at)
        return list(self.db.execute(stmt).scalars().all())
```

### 2.4 Seed Data

**Why seed data is needed:** Time slots are the "product inventory" — without them the booking system returns nothing. Existing patients + appointments are required to test/demo the existing-patient, reschedule, and cancel flows that the assessment explicitly requires.

**What gets seeded:**
- 240 time slots (2 weeks, Mon–Sat, 8:00–17:30, 30-min intervals)
- ~15% randomly marked unavailable (simulates a real schedule)
- 5 existing patients:

| Name | Phone | DOB | Insurance | Appointment |
|------|-------|-----|-----------|-------------|
| Sarah Johnson | 555-0101 | 1985-03-15 | Delta Dental | Cleaning, next Tue 10:00 |
| Michael Chen | 555-0102 | 1990-07-22 | Aetna | Checkup, next Wed 14:00 |
| Emily Rodriguez | 555-0103 | 1978-11-30 | None (self-pay) | None |
| James Williams | 555-0104 | 2000-01-10 | Cigna | Cleaning, next Fri 9:00 |
| Priya Patel | 555-0105 | 1995-06-08 | Blue Cross | None |

### 2.5 ChromaDB Collections (Embedded PersistentClient)

```python
# backend/src/vector/chroma_client.py

import chromadb
from src.config import settings

_client: chromadb.ClientAPI | None = None


def get_chroma_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=settings.CHROMA_PERSIST_DIR)
    return _client


def get_knowledge_collection():
    """dental_kb — practice info + PubMed + MedlinePlus."""
    return get_chroma_client().get_or_create_collection(
        name="dental_kb",
        metadata={"hnsw:space": "cosine"}
    )


def get_conversations_collection():
    """conversations — past patient conversation summaries."""
    return get_chroma_client().get_or_create_collection(
        name="conversations",
        metadata={"hnsw:space": "cosine"}
    )
```

**Collection 1: `dental_kb`** — Three tiers of knowledge:

```
dental_kb collection (~350-400 chunks)
├── Tier 1: Practice-specific markdown (6 files, ~30 chunks)
│   Source: data/knowledge/*.md (hand-authored)
│   Content: hours, location, insurance policies, self-pay, cancellation, FAQs
│   Metadata: {source: "practice", file: "...", section: "..."}
│
├── Tier 2: PubMed abstracts (~200-300 abstracts)
│   Source: NCBI E-utilities API (free, no auth required)
│   Content: peer-reviewed dental research abstracts
│   Metadata: {source: "pubmed", pmid: "...", title: "...", journal: "...", year: "..."}
│
└── Tier 3: MedlinePlus summaries (~30-50 topic summaries)
    Source: MedlinePlus Web Service (free, no auth required)
    Content: patient-facing dental health info from NIH
    Metadata: {source: "medlineplus", topic: "...", title: "...", url: "..."}
```

**PubMed search topics (curated):**
```python
PUBMED_DENTAL_TOPICS = [
    "dental cleaning procedure patient education",
    "dental checkup general examination",
    "dental emergency management patient",
    "tooth extraction aftercare patient",
    "root canal treatment patient guide",
    "dental crown procedure overview",
    "dental filling composite amalgam",
    "teeth whitening safety efficacy",
    "gum disease periodontal treatment",
    "dental implant procedure recovery",
    "pediatric dentistry child first visit",
    "dental anxiety management patient",
    "oral hygiene brushing flossing",
    "dental X-ray radiograph safety frequency",
    "wisdom tooth extraction recovery",
    "tooth sensitivity causes treatment",
    "dental abscess emergency treatment",
    "bruxism teeth grinding treatment",
    "fluoride treatment dental health",
    "dental sealants children prevention",
]
```

**MedlinePlus search topics:**
```python
MEDLINEPLUS_DENTAL_TOPICS = [
    "dental health", "tooth decay", "gum disease",
    "dental implants", "tooth extraction", "root canal",
    "dental emergencies", "children dental health",
    "dental X-rays", "wisdom teeth", "dry mouth",
    "oral cancer screening", "dentures", "braces orthodontics",
    "teeth whitening",
]
```

**Collection 2: `conversations`** — Conversation summaries (not raw transcripts). After each conversation ends, the LLM generates a 2-3 sentence summary, which is embedded with metadata `{patient_id, session_id, timestamp, topics}`.

### 2.6 Redis Data Structures

```
session:{session_id} → Hash {
    patient_id:     string | null,
    messages:       JSON string (array of {role, content, timestamp}),
    collected:      JSON string ({name?, phone?, dob?, insurance?}),
    intent:         string (new_patient | existing_patient | inquiry | emergency | family_booking),
    booking_state:  JSON string ({step, pending_slots[], family_members[]}),
    created_at:     timestamp,
    last_active:    timestamp
}
TTL: 30 minutes (refreshed on each message)
```

---

## 3. Agent Design

### 3.1 System Prompt

```
You are Mia, a friendly and professional dental office assistant for Bright Smile Dental.
You help patients schedule appointments, answer questions, and handle their dental care needs.

PERSONALITY:
- Warm, conversational, never robotic. Use contractions. Be concise.
- Empathetic, especially for emergencies or anxious patients.
- Proactive: suggest next steps, don't wait for the patient to drive.
- If you don't know something clinical, say so — don't guess about dental advice.

CORE WORKFLOW:
1. Greet naturally. Determine what the patient needs.
2. If booking: determine if new or existing patient.
   - New: collect full name, phone, DOB, insurance (or self-pay). Then find a slot.
   - Existing: verify identity (name + phone OR name + DOB). Then handle their request.
3. For scheduling: ask about preferred dates/times, use tools to find availability.
4. Confirm all details before finalizing any booking.

KNOWLEDGE BASE:
Your knowledge base contains three types of information:
1. Practice-specific: our hours, location, insurance policies, self-pay options.
   These are authoritative — always use them for practice questions.
2. MedlinePlus (NIH): patient-friendly dental health information.
   Prefer these for patient questions about procedures, conditions, and care.
3. PubMed research: abstracts from dental journals.
   Use these when patients ask about evidence, safety, or effectiveness.
When answering health questions, always search the knowledge base first.
Cite the source type when relevant: "According to NIH guidelines..." or
"Our office policy is..."
Never give clinical diagnoses or treatment recommendations.
Always suggest the patient discuss specifics with the dentist at their appointment.

RULES:
- NEVER fabricate appointment times. Always use get_available_slots tool.
- NEVER confirm a booking without using book_appointment tool.
- For emergencies: get a brief summary of the issue, book the earliest available slot,
  then use notify_staff with the emergency details.
- For date parsing: "next week" = the upcoming Mon-Fri, "early next month" = 1st-10th,
  "later next week" = Thu-Fri of next week. Confirm your interpretation with the patient.
- Office hours: Mon-Sat 8AM-6PM. No Sunday appointments.
- For family bookings: collect each family member's info, find back-to-back slots.
- If a time doesn't work: suggest 2-3 alternatives. If nothing works, offer to
  check a different day.
- For patients with no insurance: mention self-pay discount (15%), CareCredit financing,
  and membership plan ($299/yr). Use search_knowledge_base for details.
- Always search the knowledge base for factual questions about the practice.

Today is {date}, {day_of_week}. Current time: {time}.
```

### 3.2 Tool Definitions

| Tool | Parameters | Returns | When to Use |
|------|------------|---------|-------------|
| `search_knowledge_base` | `query: string` | Top 3 chunks with source metadata | Insurance questions, procedure info, office hours, any factual dental question |
| `search_past_conversations` | `patient_id: string, query: string` | Top 3 past conversation summaries | After identifying a patient — check prior contact history |
| `lookup_patient` | `name: string, phone?: string, dob?: string` | Patient record or "not found" | Verify existing patient. Must match name + phone or name + DOB |
| `create_patient` | `full_name, phone, dob, insurance_name?` | New patient record | After collecting all required new-patient fields |
| `get_available_slots` | `date_start, date_end, time_preference?` | List of available slots | When patient wants to see availability |
| `book_appointment` | `patient_id, slot_id, type, notes?` | Confirmation details | After patient confirms slot and type. Atomic transaction. |
| `reschedule_appointment` | `appointment_id, new_slot_id` | Updated appointment | Free old slot, claim new slot. Atomic swap. |
| `cancel_appointment` | `appointment_id` | Cancellation confirmation | Frees the slot. |
| `get_patient_appointments` | `patient_id` | List of upcoming appointments | After identifying existing patient |
| `notify_staff` | `type, message, patient_id?` | Confirmation | Emergency triage, special requests, anything needing human follow-up |

### 3.3 Conversation State Machine

```
                    ┌──────────┐
                    │  START   │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
              ┌─────│  GREET   │─────┐
              │     └────┬─────┘     │
              │          │           │
         ┌────▼───┐ ┌───▼────┐ ┌───▼──────┐
         │  NEW   │ │EXISTING│ │ INQUIRY  │
         │PATIENT │ │PATIENT │ │(no auth) │
         └───┬────┘ └───┬────┘ └───┬──────┘
             │          │          │
        ┌────▼────┐ ┌───▼───┐     │ answer from KB
        │COLLECT  │ │VERIFY │     │ (practice / PubMed
        │ INFO    │ │IDENTITY│    │  / MedlinePlus)
        │name,    │ └───┬───┘     │
        │phone,   │     │         │
        │dob,     │ ┌───▼────┐   │
        │insurance│ │PATIENT │   │
        └───┬─────┘ │ MENU   │   │
            │       │book/   │   │
            │       │resched/│   │
            │       │cancel  │   │
            │       └───┬────┘   │
            │           │        │
       ┌────▼───────────▼────┐   │
       │   SCHEDULING FLOW   │   │
       │  find slot → confirm│   │
       │  → book → done      │   │
       └─────────┬───────────┘   │
                 │               │
            ┌────▼───────────────▼──┐
            │     WRAP UP           │
            │  "Anything else?"     │
            └───────────────────────┘
```

### 3.4 Date/Time Parsing

| Expression | Interpretation |
|------------|---------------|
| "tomorrow" | next calendar day (check if office is open) |
| "next week" | Monday–Friday of the following week |
| "early next week" | Monday–Tuesday |
| "later next week" | Thursday–Friday |
| "next month" | 1st–last of next calendar month |
| "early next month" | 1st–10th |
| "late next month" | 20th–last |
| "this week" | remaining days this week |
| "ASAP" | today if available, else next available |

Agent states its interpretation ("By 'later next week' I'm looking at Thursday or Friday"), confirms with the patient, then calls the tool.

### 3.5 Family Booking Flow

1. Identify family booking intent
2. Collect/verify primary booker's info
3. Ask: "How many family members? Names and ages?"
4. For each member: name, DOB, appointment type. Kids under 18 use parent's insurance.
5. Find consecutive slots via `get_consecutive()`
6. Present as a block: "I found 10:00, 10:30, and 11:00 on Tuesday"
7. Confirm all, book atomically
8. Fallback: split across two days, or non-consecutive same-day

### 3.6 Emergency Flow

1. Patient indicates emergency → Agent responds with empathy
2. Ask for brief description (2-3 questions max)
3. Search for earliest available slot (today if possible)
4. New patient: collect minimum info (name, phone) — skip insurance
5. Book the slot
6. `notify_staff(type="emergency", message=summary)`
7. Tell patient: "I've let our dental team know. They'll be prepared when you arrive."
8. Provide relevant first-aid from knowledge base if applicable

---

## 4. Edge Cases & Error Handling

### 4.1 Conversation Edge Cases

| Scenario | Handling |
|----------|---------|
| Patient goes silent mid-conversation | Redis TTL expires after 30 min. Fresh start on return. |
| Off-topic question | Politely redirect to dental/appointment topics. |
| Conflicting info (different name/phone) | Ask to clarify, don't silently override. |
| Sunday appointment request | "We're open Mon–Sat 8AM–6PM. Would Saturday or Monday work?" |
| All slots booked for requested date | Offer 2-3 nearest available dates. |
| Double-booking race condition | SQLAlchemy transaction: check `is_available` + set `False` in one `commit()`. Postgres: `SELECT ... FOR UPDATE`. |
| No insurance provided | Optional field. Surface self-pay options and proceed. |
| Rude/hostile patient | Stay professional and empathetic. Never mirror hostility. |
| Clinical advice request | "I can't give clinical advice, but I can get you in to see the dentist." |
| Sequential SMS-style messages | Debounce: buffer 2-3 seconds, concatenate before sending to agent. |

### 4.2 Technical Error Handling

| Error | Recovery |
|-------|----------|
| LLM API timeout | Retry once (exponential backoff). After 3 failures: "Please call us at (555) 123-4567." |
| Redis connection lost | Fallback to in-memory dict for current request. Reconnect on next request. |
| ChromaDB error | Agent still functions for booking. Knowledge questions get: "I don't have that info right now — our front desk can help." |
| SQLAlchemy write conflict | Catch `IntegrityError`, retry once. If still failing: "Let me try a different time." |
| Invalid LLM tool params | Validate through Pydantic before execution. Return descriptive error to agent. |
| Expired/invalid JWT | Return 401 with `WWW-Authenticate: Bearer`. Frontend auto-refreshes and retries. |
| Concurrent slot booking | SQLAlchemy transaction ensures only one `commit()` succeeds. Loser gets "slot unavailable" → agent suggests alternatives. |
| LLM rate limit (429) | Semaphore prevents most 429s. If still hit: exponential backoff, max 2 retries. |

---

## 5. API Security (JWT) & Concurrency

### 5.1 JWT Authentication

Every API request (except token creation) requires a valid JWT bearer token. This prevents unauthorized access and ties each conversation to an authenticated session.

**Flow:**

```
1. Frontend loads → POST /api/auth/token (no auth required)
   - Can be anonymous (generates a guest token) or with patient credentials
   - Returns: { access_token: "eyJ...", token_type: "bearer", expires_in: 3600 }

2. All subsequent requests include: Authorization: Bearer <token>
   - POST /api/chat  (requires token)
   - GET  /api/slots  (requires token)
   - etc.

3. Token expires after 1 hour → frontend refreshes via POST /api/auth/refresh
```

**Why JWT for a chatbot?** Three reasons: (1) ties a session to a verified identity so patients can't access each other's data, (2) rate limiting per-token prevents abuse, (3) the token carries claims (`session_id`, `patient_id` if authenticated) so the backend doesn't need a session lookup on every request.

```python
# backend/src/api/auth.py

from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel
from src.config import settings

security = HTTPBearer()

ALGORITHM = "HS256"


class TokenData(BaseModel):
    session_id: str
    patient_id: Optional[str] = None
    exp: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600


def create_access_token(
    session_id: str,
    patient_id: str | None = None,
    expires_delta: timedelta = timedelta(hours=1),
) -> str:
    payload = {
        "session_id": session_id,
        "patient_id": patient_id,
        "exp": datetime.utcnow() + expires_delta,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> TokenData:
    """FastAPI dependency — extracts and validates JWT from Authorization header."""
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[ALGORITHM]
        )
        return TokenData(
            session_id=payload["session_id"],
            patient_id=payload.get("patient_id"),
            exp=payload["exp"],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
```

### 5.2 Concurrency Handling

A dental chatbot serving multiple locations needs to handle many simultaneous conversations without requests blocking each other or exhausting resources.

**Problem areas and solutions:**

| Bottleneck | Risk | Solution |
|-----------|------|----------|
| **LLM API calls** | Gemini has rate limits; too many parallel calls → 429s | `asyncio.Semaphore` caps concurrent LLM calls (e.g., max 10) |
| **SQLite writes** | SQLite allows only 1 writer at a time; concurrent bookings queue up | SQLAlchemy connection pool with `pool_size=5` + `StaticPool` for SQLite. In prod: Postgres handles concurrent writes natively. |
| **Double-booking race** | Two patients book the same slot simultaneously | SQLAlchemy transaction isolation. Postgres: `SELECT ... FOR UPDATE` row lock. |
| **Redis connections** | Each request opens a connection; under load → exhaustion | `redis.asyncio.ConnectionPool` with `max_connections=20` |
| **SSE streams** | Long-lived connections hold a worker thread | Uvicorn async workers handle SSE natively; each stream is a coroutine, not a blocked thread. Run with `--workers 4` for multi-process. |
| **ChromaDB queries** | Embedding + search under concurrent load | ChromaDB `PersistentClient` is thread-safe. Queries are fast (~10ms). Not a bottleneck. |

**LLM Semaphore (key pattern):**

```python
# backend/src/agent/llm.py

import asyncio
from src.config import settings

# Limit concurrent Gemini API calls to avoid rate limiting
_llm_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_LLM_CALLS)  # default: 10


async def call_gemini(messages, tools, stream=True):
    """Rate-limited Gemini call. Blocks if too many concurrent requests."""
    async with _llm_semaphore:
        # Only N calls run at a time; others await their turn
        response = await model.generate_content_async(
            messages, tools=tools, stream=stream
        )
        return response
```

**SQLAlchemy Connection Pooling:**

```python
# backend/src/db/database.py  (updated)

from sqlalchemy.pool import StaticPool

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False}
    if settings.DATABASE_URL.startswith("sqlite") else {},
    # SQLite: StaticPool shares one connection (safe with WAL mode)
    # Postgres: default QueuePool with pool_size=5, max_overflow=10
    poolclass=StaticPool
    if settings.DATABASE_URL.startswith("sqlite") else None,
    echo=settings.DEBUG,
)

# For SQLite, enable WAL mode for concurrent reads during writes
if settings.DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")    # concurrent reads
        cursor.execute("PRAGMA busy_timeout=5000")    # wait 5s on lock
        cursor.close()
```

**Redis Connection Pooling:**

```python
# backend/src/cache/redis_client.py

import redis.asyncio as aioredis
from src.config import settings

_pool: aioredis.ConnectionPool | None = None


def get_redis_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=20,
            decode_responses=True,
        )
    return _pool


async def get_redis() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=get_redis_pool())
```

**Uvicorn Workers (production):**

```bash
# Development (single worker, auto-reload)
uvicorn src.main:app --reload --port 8000

# Production (multi-worker for concurrency)
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Each worker is an independent process with its own event loop. Async routes (like SSE streaming) are non-blocking within each worker. 4 workers × hundreds of async connections = thousands of concurrent conversations.

### 5.3 FastAPI App & Endpoints

```python
# backend/src/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.routes import router
from src.api.auth_routes import auth_router
from src.db.database import init_db

app = FastAPI(title="Bright Smile Dental Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)   # /api/auth/* (no JWT required)
app.include_router(router)        # /api/chat, /api/slots (JWT required)


@app.on_event("startup")
def startup():
    init_db()


# uvicorn src.main:app --reload --port 8000
```

```python
# backend/src/api/auth_routes.py

from fastapi import APIRouter
from pydantic import BaseModel
from src.api.auth import create_access_token, TokenResponse
import uuid

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenRequest(BaseModel):
    session_id: str | None = None  # None = generate new


@auth_router.post("/token", response_model=TokenResponse)
def get_token(req: TokenRequest):
    """Issue a JWT. No auth required — this is the entry point."""
    session_id = req.session_id or uuid.uuid4().hex
    token = create_access_token(session_id=session_id)
    return TokenResponse(access_token=token)


@auth_router.post("/refresh", response_model=TokenResponse)
def refresh_token(token_data=Depends(verify_token)):
    """Refresh an existing token. Requires valid (non-expired) JWT."""
    token = create_access_token(
        session_id=token_data.session_id,
        patient_id=token_data.patient_id,
    )
    return TokenResponse(access_token=token)
```

```python
# backend/src/api/routes.py

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from src.api.auth import verify_token, TokenData
from src.db.database import get_db

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str  # session_id now comes from JWT, not request body


@router.post("/chat")
async def chat(
    req: ChatRequest,
    token: TokenData = Depends(verify_token),  # JWT required
    db=Depends(get_db),
):
    """SSE streaming chat. Session ID extracted from JWT."""
    async def event_stream():
        async for chunk in agent.run(token.session_id, req.message, db):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/slots")
def get_slots(
    date: str = None,
    token: TokenData = Depends(verify_token),
    db=Depends(get_db),
):
    """Available slots. JWT required."""
    ...
```

**Frontend token flow (Next.js):**

```typescript
// frontend/src/lib/api.ts

let accessToken: string | null = null;

async function getToken(): Promise<string> {
  if (accessToken) return accessToken;

  const sessionId = localStorage.getItem("session_id") || crypto.randomUUID();
  localStorage.setItem("session_id", sessionId);

  const res = await fetch(`${API_URL}/api/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  const data = await res.json();
  accessToken = data.access_token;

  // Schedule refresh before expiry (e.g., at 50 min mark)
  setTimeout(() => refreshToken(), 50 * 60 * 1000);

  return accessToken!;
}

async function sendMessage(message: string) {
  const token = await getToken();
  return fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ message }),
  });
}
```

---

## 6. Project Structure

```
dental-chatbot/
├── README.md
├── .env.example
├── docker-compose.yml                # Redis only (ChromaDB is embedded)
│
├── backend/
│   ├── pyproject.toml                # or requirements.txt
│   ├── src/
│   │   ├── __init__.py
│   │   ├── main.py                   # FastAPI app — uvicorn entrypoint
│   │   ├── config.py                 # Pydantic BaseSettings
│   │   │
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── routes.py            # POST /api/chat, GET /api/slots (JWT protected)
│   │   │   ├── auth.py              # JWT create/verify helpers
│   │   │   └── auth_routes.py       # POST /api/auth/token, /api/auth/refresh
│   │   │
│   │   ├── agent/
│   │   │   ├── __init__.py
│   │   │   ├── orchestrator.py       # ReAct loop
│   │   │   ├── system_prompt.py      # Prompt construction
│   │   │   ├── llm.py                # Gemini 2.0 Flash client
│   │   │   ├── tools/
│   │   │   │   ├── __init__.py       # Tool registry
│   │   │   │   ├── knowledge.py      # search_knowledge_base
│   │   │   │   ├── conversations.py  # search_past_conversations
│   │   │   │   ├── patients.py       # lookup, create
│   │   │   │   ├── appointments.py   # slots, book, reschedule, cancel
│   │   │   │   └── notifications.py  # notify_staff
│   │   │   └── date_parser.py        # Natural language → ISO date range
│   │   │
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── database.py           # Engine, SessionLocal, get_db()
│   │   │   ├── models.py             # SQLAlchemy 2.0 models
│   │   │   └── repositories.py       # PatientRepo, SlotRepo, AppointmentRepo
│   │   │
│   │   ├── vector/
│   │   │   ├── __init__.py
│   │   │   ├── chroma_client.py      # PersistentClient (embedded, no server)
│   │   │   └── embeddings.py         # Embedding helper
│   │   │
│   │   ├── cache/
│   │   │   ├── __init__.py
│   │   │   ├── redis_client.py       # Async Redis connection
│   │   │   └── session.py            # Session state CRUD
│   │   │
│   │   └── schemas/
│   │       └── __init__.py           # Pydantic request/response models
│   │
│   ├── data/
│   │   ├── knowledge/                # Practice-specific docs (Tier 1)
│   │   │   ├── insurance_policy.md
│   │   │   ├── procedures.md
│   │   │   ├── office_info.md
│   │   │   ├── emergency_protocol.md
│   │   │   ├── faq.md
│   │   │   └── family_booking.md
│   │   └── chroma/                   # ChromaDB persistent storage (gitignored)
│   │
│   ├── scripts/
│   │   ├── seed.py                   # DB + slots + patients
│   │   ├── embed_knowledge.py        # Pull APIs + embed (--refresh to re-pull)
│   │   └── test_scenarios.py         # Automated conversation tests
│   │
│   └── tests/
│       ├── test_repositories.py
│       ├── test_tools.py
│       └── test_agent.py
│
├── frontend/
│   ├── package.json
│   ├── next.config.js
│   ├── tsconfig.json
│   ├── .env.local                    # NEXT_PUBLIC_API_URL=http://localhost:8000
│   └── src/
│       ├── app/
│       │   ├── layout.tsx
│       │   └── page.tsx
│       └── components/
│           ├── ChatWindow.tsx
│           ├── MessageBubble.tsx
│           ├── TypingIndicator.tsx
│           ├── QuickReplies.tsx
│           ├── AppointmentCard.tsx
│           └── ChatInput.tsx
│
└── .gitignore                        # .venv/, data/chroma/, data/dental.db, .env, __pycache__/
```

---

## 7. Implementation TODO

### Phase 0: Project Bootstrap [~30 min]

- [ ] **0.1** Create monorepo: `backend/`, `frontend/`, root `docker-compose.yml`
- [ ] **0.2** Init Python backend:
  - Create venv:
    ```bash
    cd backend
    python -m venv .venv
    source .venv/bin/activate    # macOS/Linux
    # .venv\Scripts\activate     # Windows
    ```
  - Create `requirements.txt`:
    ```
    fastapi>=0.110
    uvicorn[standard]
    sqlalchemy>=2.0
    pydantic-settings
    google-generativeai
    chromadb
    redis[hiredis]
    python-jose[cryptography]
    python-dotenv
    requests
    beautifulsoup4
    lxml
    ```
  - `pip install -r requirements.txt`
  - Create `src/main.py` with bare FastAPI app + CORS
  - Add `.venv/` to `.gitignore`
  - Verify: `uvicorn src.main:app --reload --port 8000` → Swagger at `/docs`
- [ ] **0.3** Init Next.js frontend:
  - `npx create-next-app@latest frontend --typescript --tailwind --app`
  - Set `NEXT_PUBLIC_API_URL=http://localhost:8000` in `.env.local`
  - Verify: `npm run dev` → page on port 3000
- [ ] **0.4** `.env.example`:
  ```
  GEMINI_API_KEY=
  DATABASE_URL=sqlite:///./data/dental.db
  REDIS_URL=redis://localhost:6379
  CHROMA_PERSIST_DIR=./data/chroma
  JWT_SECRET_KEY=your-secret-key-change-in-production
  MAX_CONCURRENT_LLM_CALLS=10
  DEBUG=true
  ```
- [ ] **0.5** `docker-compose.yml` (Redis only):
  ```yaml
  services:
    redis:
      image: redis:7-alpine
      ports: ["6379:6379"]
  ```
- [ ] **0.6** `README.md` skeleton
- [ ] **0.7** Verify: frontend fetches from backend, CORS works

---

### Phase 1: Data Layer [~2.5 hr]

**1A — SQLAlchemy + SQLite**

- [ ] **1A.1** `src/config.py` — Pydantic `BaseSettings` loading `.env`
- [ ] **1A.2** `src/db/database.py` — engine, `SessionLocal`, `init_db()`, `get_db()`
- [ ] **1A.3** `src/db/models.py` — Patient, TimeSlot, Appointment, ConversationLog
- [ ] **1A.4** `src/db/repositories.py` — PatientRepo, SlotRepo, AppointmentRepo with all query methods
- [ ] **1A.5** Test: `init_db()` → verify `.db` file created with tables
- [ ] **1A.6** `scripts/seed.py`:
  - 2 weeks of 30-min slots (Mon–Sat, 8:00–17:30, skip Sundays)
  - ~15% randomly unavailable
  - 5 patients from §2.4, 3 with existing appointments
- [ ] **1A.7** Run: `python -m scripts.seed` → verify with repo queries

**1B — ChromaDB (Embedded) + Knowledge Ingestion**

- [ ] **1B.1** `src/vector/chroma_client.py` — `PersistentClient`, collection getters, search/store helpers
- [ ] **1B.2** `src/vector/embeddings.py` — embedding via Gemini `text-embedding-004` or ChromaDB default
- [ ] **1B.3** Write 6 practice-specific markdown files in `data/knowledge/`
- [ ] **1B.4** `scripts/embed_knowledge.py` — core setup script:
  - Load practice markdown → chunk by headers (~300 tokens, 50 overlap)
  - Pull PubMed abstracts via E-utilities API (20 topics × 15 abstracts)
  - Pull MedlinePlus summaries via Web Service (15 topics)
  - Embed all ~350-400 docs → upsert into `dental_kb` collection
  - Support `--refresh` flag to re-pull and rebuild
- [ ] **1B.5** Run: `python -m scripts.embed_knowledge` → test queries:
  - "what insurance do you accept?" → practice docs
  - "is teeth whitening safe?" → MedlinePlus/PubMed
  - "what to expect from a root canal?" → MedlinePlus

**1C — Redis**

- [ ] **1C.1** `src/cache/redis_client.py` — async `redis.asyncio.from_url()`
- [ ] **1C.2** `src/cache/session.py`:
  - `get_session()`, `update_session()`, `add_message()`, `clear_session()`
  - 30-min TTL refresh on every update
- [ ] **1C.3** Fallback: if Redis unavailable, use in-memory dict
- [ ] **1C.4** Test: create session, add messages, verify TTL

---

### Phase 2: Agent Core [~2 hr]

**2A — Gemini 2.0 Flash**

- [ ] **2A.1** `src/agent/llm.py`:
  - `google.generativeai` configured with API key
  - Model: `gemini-2.0-flash`
  - Function declarations array (Gemini tool calling format)
  - Streaming: `model.generate_content(stream=True, tools=tools)`
  - Retry with exponential backoff (max 2)
- [ ] **2A.2** Test: completion + function call round-trip

**2B — Tools**

- [ ] **2B.1** Pydantic input schemas for each tool
- [ ] **2B.2** Implement all 10 tool handlers:
  - `search_knowledge_base` → ChromaDB `dental_kb`
  - `search_past_conversations` → ChromaDB `conversations`
  - `lookup_patient` → PatientRepository
  - `create_patient` → PatientRepository
  - `get_available_slots` → SlotRepository
  - `book_appointment` → AppointmentRepository (transactional)
  - `reschedule_appointment` → AppointmentRepository (atomic swap)
  - `cancel_appointment` → AppointmentRepository
  - `get_patient_appointments` → AppointmentRepository
  - `notify_staff` → console log + notification store
- [ ] **2B.3** Tool registry: `dict[str, ToolDef]` mapping name → schema + handler
- [ ] **2B.4** Validation: parse args through Pydantic, return errors to agent

**2C — Orchestrator (ReAct Loop)**

- [ ] **2C.1** `src/agent/orchestrator.py`:
  ```
  async def run(session_id, user_message, db) -> AsyncGenerator:
      1. Load/create session from Redis
      2. Append user message to history
      3. Build prompt: system_prompt + history
      4. Call Gemini with function declarations
      5. While response has function_calls (max 5 iterations):
         a. Execute each via tool registry
         b. Build function_response parts
         c. Call Gemini again with responses
      6. Yield text chunks from final response (streaming)
      7. Update session in Redis
  ```
- [ ] **2C.2** `src/agent/system_prompt.py` — base prompt + inject date/time + patient context
- [ ] **2C.3** Context management: keep last 20 messages, summarize older if nearing token limit
- [ ] **2C.4** Max iterations guard: force text response after 5 tool rounds
- [ ] **2C.5** Conversation end: on wrap-up →
  - Summarize via Gemini
  - Store in ChromaDB `conversations`
  - Log to SQLAlchemy `ConversationLog`
  - Clear Redis session

**2D — Date Parsing**

- [ ] **2D.1** `src/agent/date_parser.py` — natural language → `{start, end}` ISO range
- [ ] **2D.2** Test edge cases: Sunday, month boundaries, "ASAP", "later next week"

---

### Phase 3: API Security, Concurrency & Streaming [~1.5 hr]

**3A — JWT Authentication**

- [ ] **3A.1** `src/api/auth.py` — `create_access_token()`, `verify_token()` FastAPI dependency
- [ ] **3A.2** `src/api/auth_routes.py`:
  - `POST /api/auth/token` — issue JWT (no auth required, accepts optional `session_id`)
  - `POST /api/auth/refresh` — refresh existing token (requires valid JWT)
- [ ] **3A.3** Protect all `/api/chat` and `/api/slots` routes with `Depends(verify_token)`
- [ ] **3A.4** Extract `session_id` from JWT claims instead of request body
- [ ] **3A.5** Token expiry: 1 hour, frontend auto-refreshes at 50 min
- [ ] **3A.6** Test: request without token → 401; request with valid token → 200; expired token → 401

**3B — Concurrency**

- [ ] **3B.1** LLM semaphore (`src/agent/llm.py`): `asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)` wrapping all Gemini calls
- [ ] **3B.2** SQLAlchemy connection pooling: `StaticPool` for SQLite + WAL mode + `busy_timeout=5000`
- [ ] **3B.3** Redis connection pool: `ConnectionPool.from_url(max_connections=20)`
- [ ] **3B.4** Verify concurrent safety: two simultaneous booking requests for the same slot → only one succeeds

**3C — Streaming & Wiring**

- [ ] **3C.1** Wire `POST /api/chat` → orchestrator → `StreamingResponse` (SSE)
- [ ] **3C.2** SSE format: `data: {"type": "text", "content": "..."}\n\n` + `data: [DONE]\n\n`
- [ ] **3C.3** CORS: allow `http://localhost:3000`
- [ ] **3C.4** `startup` event: `init_db()`, connect Redis pool, init ChromaDB client
- [ ] **3C.5** Error handling: catch LLM/SQLAlchemy/Redis errors → stream fallback message
- [ ] **3C.6** Rate limiting: 10 msg/min per token (keyed on JWT `session_id`)
- [ ] **3C.7** Test with curl:
  ```bash
  # Get a token
  TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/token \
    -H "Content-Type: application/json" \
    -d '{}' | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

  # Chat with token
  curl -N -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $TOKEN" \
    -d '{"message": "Hi, I need a cleaning"}'
  ```

---

### Phase 4: Frontend [~1.5 hr]

- [ ] **4.1** `ChatWindow.tsx` — main container, message state, SSE connection
- [ ] **4.2** `MessageBubble.tsx` — assistant (left) / user (right) styling
- [ ] **4.3** `ChatInput.tsx` — input + send, Enter/Shift+Enter
- [ ] **4.4** `TypingIndicator.tsx` — animated dots during agent processing
- [ ] **4.5** `QuickReplies.tsx` — contextual chips (optional)
- [ ] **4.6** `AppointmentCard.tsx` — confirmation card (date, time, type)
- [ ] **4.7** API client layer (`src/lib/api.ts`):
  - `getToken()` — POST `/api/auth/token`, cache in memory, schedule refresh at 50 min
  - `sendMessage(message)` — POST `/api/chat` with `Authorization: Bearer <token>`
  - SSE reader: parse `data:` lines from `ReadableStream`, append chunks incrementally
  - Handle 401 → auto-refresh token and retry
  - Handle fetch errors / disconnects gracefully
- [ ] **4.8** Session ID: `crypto.randomUUID()` → localStorage → sent in token request
- [ ] **4.9** Auto-scroll, mobile-responsive, welcome message

---

### Phase 5: Integration Testing [~1 hr]

- [ ] **5.1** New patient full flow (greet → collect info → book cleaning)
- [ ] **5.2** Existing patient reschedule (verify → show appointments → reschedule)
- [ ] **5.3** Emergency booking (empathy → triage → earliest slot → staff notification)
- [ ] **5.4** Family booking (parent + 2 kids, back-to-back)
- [ ] **5.5** No insurance patient (self-pay options surfaced)
- [ ] **5.6** Fully booked date (alternatives offered)
- [ ] **5.7** Off-topic → polite redirect
- [ ] **5.8** Subjective dates ("later next week", "early next month")
- [ ] **5.9** Existing patient cancellation
- [ ] **5.10** General inquiry → verify RAG returns correct info from all 3 sources
- [ ] **5.11** Knowledge base query: "is teeth whitening safe?" → should cite PubMed/MedlinePlus
- [ ] **5.12** Auth test: request without token → 401; expired token → 401; valid token → works
- [ ] **5.13** Concurrency test: fire 5 simultaneous booking requests for the same slot → exactly 1 succeeds, 4 get "slot unavailable"
- [ ] **5.14** Fix issues found

---

### Phase 6: Polish & Docs [~30 min]

- [ ] **6.1** `README.md`:
  - Architecture diagram
  - Tech stack + rationale
  - Setup:
    ```bash
    docker-compose up -d                        # Redis

    cd backend
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env                        # Edit with your GEMINI_API_KEY
    python -m scripts.seed                      # DB + slots + patients
    python -m scripts.embed_knowledge           # PubMed + MedlinePlus + practice docs
    uvicorn src.main:app --reload --port 8000

    cd ../frontend
    npm install && npm run dev
    ```
  - Knowledge refresh: `python -m scripts.embed_knowledge --refresh`
  - Design decisions, prioritization, what I'd build next
- [ ] **6.2** Code cleanup, docstrings
- [ ] **6.3** Record demo video (3-4 scenarios, one challenge, prioritization)
- [ ] **6.4** Push to GitHub

---

## 8. Prioritization Rationale

### Load-bearing (build first)

1. **New patient booking** — primary conversion. Collection → slot lookup → book → confirm.
2. **Emergency flow** — highest patient impact. Fast path to earliest slot + staff notification.
3. **Existing patient scheduling** — second most common. Identity verification gates everything.
4. **Knowledge base with real dental sources** — PubMed + MedlinePlus ground the agent in authoritative info instead of hallucinating. This is the differentiator.
5. **Subjective date handling** — explicitly required, common chatbot failure mode.

### Nice-to-have (if time permits)

6. **Family booking** — lower frequency, works with existing tools + prompt guidance.
7. **Past conversation recall** — ChromaDB `conversations` collection. Meaningful but not demo-critical.
8. **Quick reply chips** — better UX, not required.
9. **SMS debouncing** — important at scale, unlikely in demo.

### Production (out of scope)

- Postgres (connection string swap + `SELECT ... FOR UPDATE` + Alembic migrations)
- Real auth, HIPAA compliance, encryption at rest
- Webhook integrations (Slack, SMS, practice management)
- Scheduled knowledge refresh cron (weekly `--refresh`)
- Observability, analytics, eval suite
- Multi-provider scheduling, automated follow-ups
- Load testing for 10K+ concurrent conversations

### Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| LLM hallucinating times | **Critical** | Tools enforce reality. Prompt forbids fabrication. |
| Double-booking | **High** | SQLAlchemy transaction. Postgres: `FOR UPDATE`. |
| Agent loops | **Medium** | Max 5 iterations. Cost cap. |
| Emergency not escalated | **High** | Prompt requires `notify_staff`. |
| Stale knowledge base | **Low** | Medical knowledge changes slowly. `--refresh` flag for re-pull. |
| PubMed/MedlinePlus API down during setup | **Low** | Graceful skip: embed what's available, log failures, retry later. |

---

## 9. Tech Stack Summary

| Component | Technology | Why |
|-----------|-----------|-----|
| Frontend | Next.js 15 + Tailwind | Fast scaffold, clean chat UI |
| Backend | FastAPI + Uvicorn | Async Python, native SSE, Pydantic validation |
| Auth | JWT (`python-jose`) | Stateless, token carries session_id, 1hr expiry with refresh |
| Concurrency | asyncio semaphore + connection pools | Caps LLM calls, pools DB/Redis connections, WAL mode for SQLite |
| ORM | SQLAlchemy 2.0 | Declarative models, dialect-agnostic, transactional |
| Relational DB | SQLite (dev) / Postgres (prod) | Zero setup. `DATABASE_URL` swap for prod. |
| Vector store | ChromaDB (PersistentClient, embedded) | In-process, no server, Python-native |
| Knowledge sources | PubMed E-utilities + MedlinePlus Web Service | Free, authoritative, real dental/medical info |
| Cache | Redis (`redis-py` async) | Sub-ms session state, TTL |
| LLM | Gemini 2.0 Flash | Free tier, fast, solid function calling |
| Agent | ReAct (custom orchestrator) | Flexible for unpredictable conversations |
| Languages | Python 3.11+ (backend), TypeScript (frontend) | Python for AI/data, TS for UI |

---

## 10. Dev Commands

```bash
# Start Redis
docker-compose up -d

# Backend setup
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                        # Edit with your GEMINI_API_KEY + JWT_SECRET_KEY
python -m scripts.seed                      # Create DB + time slots + patients
python -m scripts.embed_knowledge           # Pull PubMed + MedlinePlus + embed all
uvicorn src.main:app --reload --port 8000   # Start API (dev, single worker)

# Frontend setup
cd frontend
npm install
npm run dev                                  # Start on port 3000

# Verify
curl http://localhost:8000/docs              # FastAPI Swagger
open http://localhost:3000                    # Chat UI

# Quick API test (get token + chat)
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/token \
  -H "Content-Type: application/json" -d '{}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "Hi!"}'

# Refresh knowledge (re-pull from APIs)
cd backend
python -m scripts.embed_knowledge --refresh

# Production (multi-worker)
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
```
