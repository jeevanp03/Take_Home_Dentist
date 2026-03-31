
# Dental Practice Chatbot

An agentic AI assistant that handles patient intake, appointment booking/rescheduling/cancellation, and dental knowledge Q&A for dental practices.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15 (App Router) — port 3000 |
| Backend | FastAPI + Uvicorn (Python 3.11+) — port 8000 |
| Database | SQLite (dev) / Postgres (prod) via SQLAlchemy 2.0 |
| Vector DB | ChromaDB embedded (PersistentClient, in-process) |
| Cache | Redis 7 for conversation state (30-min TTL) |
| LLM | Gemini 2.5 Flash via google-genai SDK |
| Agent | ReAct loop with 11 tools |

## Quick Start (Docker Compose)

The fastest way to run the full stack — one command starts Redis, backend, and frontend:

```bash
# 1. Clone and configure
git clone <repo-url> && cd <repo>
cp .env.example .env
```

Edit `.env` — you only need to set two values:

| Variable | How to get it |
|----------|--------------|
| `GEMINI_API_KEY` | Free at [Google AI Studio](https://aistudio.google.com/apikey) — sign in, click "Create API Key" |
| `JWT_SECRET_KEY` | Generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

```bash
# 2. Build and start everything
docker compose up --build

# 3. In a separate terminal — seed DB + embed knowledge base
docker compose exec backend python -m scripts.seed
docker compose exec backend python -m scripts.embed_knowledge
```

Open **http://localhost:3000** — that's it.

Docker Compose orchestrates three services with health checks:
- **redis** starts first (healthcheck: `redis-cli ping`)
- **backend** waits for Redis to be healthy, then starts FastAPI on port 8000 (healthcheck: `/api/health`)
- **frontend** waits for backend to be healthy, then starts Next.js on port 3000

```bash
# Run in background
docker compose up -d --build
docker compose logs -f          # tail all logs
docker compose logs -f backend  # tail backend only

# Rebuild after code changes
docker compose up --build

# Stop everything
docker compose down

# Stop and remove volumes (fresh start)
docker compose down -v
```

## Local Development Setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- Redis (optional — falls back to in-memory dict if unavailable)

### 1. Environment

```bash
cp .env.example .env
cp apps/frontend/.env.local.example apps/frontend/.env.local
```

Edit `.env` and set `GEMINI_API_KEY` and `JWT_SECRET_KEY` (see table above). Everything else has working defaults.

> **Note:** The frontend has its own env file (`apps/frontend/.env.local`) because Next.js only reads `NEXT_PUBLIC_*` vars from its own directory. The default `NEXT_PUBLIC_API_URL=http://localhost:8000` works out of the box.

### 2. Backend

```bash
cd apps/backend
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt   # includes requirements.txt + pytest

# Seed the database with sample patients, time slots, and appointments
python -m scripts.seed

# Embed knowledge base into ChromaDB (see Knowledge Base section below)
python -m scripts.embed_knowledge

# Start the server
uvicorn src.main:app --reload --port 8000
```

### 3. Frontend

```bash
cd apps/frontend
npm install
npm run dev
```

### 4. Redis (optional)

```bash
# Option A: via Docker
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Option B: via Homebrew (macOS)
brew install redis && brew services start redis
```

If Redis is unavailable, the backend automatically falls back to an in-memory dict. Fine for dev, but conversations won't persist across restarts.

### 5. Verify

- Backend health: http://localhost:8000/api/health
- API docs: http://localhost:8000/docs
- Frontend: http://localhost:3000

## Knowledge Base Setup (Vector DB)

The chatbot's RAG pipeline uses ChromaDB with three tiers of dental knowledge:

| Tier | Source | Content |
|------|--------|---------|
| 1 — Practice | Local markdown in `data/knowledge/` | Office hours, insurance, procedures, FAQ, emergency protocol |
| 2 — MedlinePlus | NLM API (cached) | Patient-friendly health topic summaries (19 topics) |
| 3 — PubMed | NCBI API (cached) | Research abstracts for clinical depth (20 topics, 8 articles each) |

ChromaDB uses the default `all-MiniLM-L6-v2` embedding model — runs locally, no API calls or credits needed.

### Embedding Commands

Run from `apps/backend/` (or via `docker compose exec backend ...`):

```bash
# Default: embed everything (uses cached API responses if available, fetches if not)
python -m scripts.embed_knowledge

# Practice markdown only (fastest — no external API calls)
python -m scripts.embed_knowledge --practice-only

# Re-embed from cache only (skip API calls)
python -m scripts.embed_knowledge --refresh

# Re-fetch everything from APIs, update cache, then re-embed
python -m scripts.embed_knowledge --repull
```

**What the pipeline does:**
1. Chunks practice markdown files by `##` headers
2. Fetches PubMed abstracts (20 topics, 8 articles each, quality-filtered)
3. Fetches MedlinePlus summaries (19 topics, with curated fallbacks)
4. Embeds all chunks into ChromaDB's `dental_kb` collection
5. Runs deduplication (removes chunks with cosine similarity > 0.92)

API responses are cached in `data/knowledge/cache/` so subsequent runs don't re-fetch.

### Docker Note

The ChromaDB data directory is volume-mounted (`./apps/backend/data:/app/data`), so embeddings persist across container restarts. Embed once and you're set:

```bash
docker compose exec backend python -m scripts.embed_knowledge
```

## Scripts Reference

| Command (from `apps/backend/`) | Docker equivalent | What it does |
|-------------------------------|-------------------|-------------|
| `python -m scripts.seed` | `docker compose exec backend python -m scripts.seed` | Seed DB with time slots, sample patients, appointments. Idempotent. |
| `python -m scripts.embed_knowledge` | `docker compose exec backend python -m scripts.embed_knowledge` | Embed all knowledge (practice + PubMed + MedlinePlus). |
| `python -m scripts.embed_knowledge --practice-only` | `docker compose exec backend python -m scripts.embed_knowledge --practice-only` | Embed local practice markdown only (fast, no API calls). |
| `python -m scripts.embed_knowledge --refresh` | `docker compose exec backend python -m scripts.embed_knowledge --refresh` | Re-embed from cached API responses only. |
| `python -m scripts.embed_knowledge --repull` | `docker compose exec backend python -m scripts.embed_knowledge --repull` | Re-fetch all APIs, update cache, re-embed. |

## Testing

```bash
cd apps/backend

# Unit tests (no external API calls)
.venv/bin/python -m pytest tests/ -v

# Integration tests (requires GEMINI_API_KEY)
.venv/bin/python -m pytest tests/ -m integration -v -s
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/token` | No | Get JWT token (starts session) |
| POST | `/api/auth/refresh` | Yes | Refresh expiring token |
| POST | `/api/identify` | Yes | Pre-agent patient identification |
| POST | `/api/chat` | Yes | Send message, receive SSE stream |
| GET | `/api/slots` | Yes | Query available appointment slots |
| POST | `/api/feedback` | Yes | Submit thumbs up/down on messages |
| GET | `/api/health` | No | Health check with service status |

### SSE Stream Format

`POST /api/chat` returns `text/event-stream`:

```
data: {"type": "text", "content": "Hello! I'm Mia..."}
data: {"type": "text", "content": "Let me check that for you."}
data: {"type": "tool_status", "content": "Searching for available slots..."}
data: {"type": "tool_status", "content": "Putting it all together..."}
data: {"type": "text", "content": "Here are your options: ..."}
data: [DONE]
```

## Architecture

### System Overview

Fully decoupled frontend/backend connected via HTTP + SSE. Either layer can be swapped independently.

```
┌─────────────┐     HTTP/SSE      ┌──────────────────────────────────────────┐
│  Next.js 15  │◄────────────────►│              FastAPI Backend              │
│  (Chat UI)   │                  │                                          │
│  Port 3000   │                  │  ┌──────────────────────────────────┐    │
└─────────────┘                  │  │  ReAct Agent (Orchestrator)      │    │
                                  │  │  - 11 tools, max 5 iterations   │    │
                                  │  │  - Session-level mutex           │    │
                                  │  │  - Gemini 2.5 Flash (temp=0.4)  │    │
                                  │  └──────────┬───────────────────────┘    │
                                  │             │                            │
                                  │  ┌──────────▼───────────────────────┐    │
                                  │  │           Tool Layer              │    │
                                  │  │  knowledge · patients · slots    │    │
                                  │  │  appointments · conversations    │    │
                                  │  │  practice_info · notifications   │    │
                                  │  └──┬──────────┬──────────┬────────┘    │
                                  │     │          │          │              │
                                  │  ┌──▼──┐  ┌───▼───┐  ┌──▼───┐         │
                                  │  │SQLite│  │ChromaDB│  │Redis │         │
                                  │  │(WAL) │  │(embed) │  │(cache)│         │
                                  │  └──────┘  └───────┘  └──────┘         │
                                  └──────────────────────────────────────────┘
```

### Agent Tools (11)

| Tool | Purpose |
|------|---------|
| `search_knowledge_base` | RAG over dental_kb (top-5 → MMR → top-3, similarity threshold) |
| `search_past_conversations` | RAG over conversations collection, filtered by patient_id |
| `lookup_patient` | Find existing patient by name + phone or name + DOB |
| `create_patient` | Register new patient |
| `get_available_slots` | Query open appointment slots by date range + time preference |
| `book_appointment` | Book an appointment (transactional) |
| `reschedule_appointment` | Atomic slot swap |
| `cancel_appointment` | Cancel and free the slot |
| `get_patient_appointments` | List patient's appointments |
| `notify_staff` | Alert staff (emergencies, escalations, special requests) |
| `get_practice_info` | Static practice details (no vector search, instant) |

### Key Design Decisions

- **Deterministic before non-deterministic** — patient identification (form) happens before the LLM agent starts, so the agent always has full context from turn 1
- **Decoupled frontend/backend** — either can be swapped independently via HTTP + SSE
- **ChromaDB in-process** — no separate server needed; adequate for ~400 dental docs
- **Redis as buffer, not source of truth** — conversations flush to ChromaDB + SQLite on session end
- **Concurrency via async + semaphores**, not threads; SQLite WAL for concurrent reads
- **SMS-style debouncing** — 2-3s buffer concatenates rapid messages before dispatching to agent
- **Source-weighted retrieval** — practice docs prioritized over PubMed for office-specific questions
- **Retrieval pipeline** — top-5 from ChromaDB → MMR for diversity → return top-3 (reject cosine distance > 0.5)
- **Practice queries bypass vector search** — `get_practice_info` returns static data instantly

## Prioritization

This section explains what was prioritized and why, framed as shipping to production for hundreds of locations and 10k+ conversations/day.

### What's Load-Bearing (built first, tested hardest)

1. **Never hallucinate appointments or medical info.** The agent only surfaces times from `get_available_slots` (DB query) and medical info from `search_knowledge_base` (RAG with similarity threshold). If the knowledge base returns nothing relevant, Mia says "I'll need to check on that" instead of guessing. This is the single most important property of the system — a fabricated appointment time or incorrect medical guidance erodes all patient trust instantly.

2. **Double-booking prevention.** `book_appointment` is transactional: marks the slot unavailable and creates the appointment in one commit. The repository retries once on `IntegrityError`, then returns a clean error. Concurrent bookings for the same slot are tested (only one succeeds). At 10k conversations/day across hundreds of locations, race conditions aren't theoretical — they're guaranteed.

3. **911/ER escalation.** The system prompt hard-codes escalation criteria (airway compromise, uncontrolled bleeding, facial trauma, jaw fracture). For these, Mia tells the patient to call 911 immediately and does NOT attempt to book an appointment. This is non-negotiable — the chatbot must never delay emergency care by trying to schedule it.

4. **Patient data isolation.** Cross-patient operations are blocked at the tool level: `book_appointment`, `get_patient_appointments`, `reschedule_appointment`, and `cancel_appointment` all verify the `patient_id` matches the session. The agent can't accidentally expose one patient's data to another.

5. **Graceful degradation.** Redis down? In-memory fallback. ChromaDB empty? Practice info tool still works. LLM safety-blocked? Static fallback message with the office phone number. Every external dependency has a failure path that keeps the patient informed.

### What's Nice-to-Have (built, but not load-bearing)

- **SMS debouncing** — concatenates rapid messages, but the system works fine without it
- **Conversation persistence to ChromaDB** — enables "search past conversations" but isn't critical for core flows
- **Thumbs up/down feedback** — logs for now, not yet wired to model improvement
- **MedlinePlus/PubMed RAG tiers** — practice markdown alone covers 80% of questions; external sources add depth
- **Post-conversation summarization** — Gemini generates a summary on goodbye, stored for future context

### Risk and Failure Mode Thinking

| Failure mode | Mitigation |
|---|---|
| LLM hallucinates appointment times | Tool-only slot access; agent cannot invent slots |
| LLM gives medical advice | System prompt forbids diagnosis/prescription; knowledge base provides sourced info only |
| Double booking under concurrency | Transactional DB writes with `IntegrityError` retry |
| Patient sees another patient's data | Session-level `patient_id` enforcement on all tools |
| LLM gets stuck in tool loop | Max 5 iterations, then forced text-only response |
| LLM completely fails (safety block, timeout) | Static fallback: "You can reach us at (555) 123-4567" |
| Redis goes down mid-conversation | In-memory fallback preserves session for current process lifetime |
| Partial booking state (booked but agent crashed before confirming) | Booking is committed to DB immediately; patient can call office to verify |
| Prompt injection | Anti-injection hardening in system prompt; input sanitization (control chars stripped, 2000 char limit) |

### PHI Handling — Current State and Production Requirements

**Current (dev):** SQLite stores patient names, phones, DOBs, insurance, and appointment history in plaintext. Conversation logs may contain clinical details. This is acceptable for a demo but not for production.

**Production would need:**
- Encryption at rest: SQLCipher or PostgreSQL with pgcrypto, encrypted volume for ChromaDB
- Redis AUTH + TLS (currently unauthenticated)
- BAA with Google for Gemini API (or switch to Vertex AI / self-hosted LLM)
- Structured HIPAA audit logging (who accessed what, when)
- Data retention policy for conversation logs
- Application-layer encryption for `conversation_logs.messages`
- Access controls beyond JWT (role-based, IP allowlisting)

## What I'd Build Next

If continuing development, in priority order:

1. **PostgreSQL migration** — SQLite doesn't scale to multiple workers. One `alembic` migration away.
2. **Multi-provider scheduling** — currently single-provider (Dr. Smith). Add provider selection and availability per provider.
3. **Appointment reminders** — SMS/email notifications via Twilio/SendGrid, triggered by a cron job.
4. **Conversation analytics dashboard** — aggregate feedback, common questions, booking success rate.
5. **Multi-language support** — Gemini handles Spanish well; add language detection and bilingual system prompt.
6. **Webhook-based staff notifications** — replace in-memory store with Slack/Teams/PagerDuty integration.
7. **Patient portal integration** — SSO with existing practice management software (Open Dental, Dentrix).

## Project Structure

```
├── docker-compose.yml          # Full stack: Redis + Backend + Frontend
├── .env.example                # Environment variable template
├── apps/
│   ├── backend/
│   │   ├── Dockerfile          # Python 3.11-slim, pip cache mount
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   ├── main.py         # FastAPI app, CORS, startup lifecycle
│   │   │   ├── config.py       # Pydantic BaseSettings from .env
│   │   │   ├── api/            # Routes, JWT auth, debounce middleware
│   │   │   ├── agent/          # ReAct orchestrator, system prompt, LLM client, tools/
│   │   │   ├── db/             # SQLAlchemy models, database setup, repositories
│   │   │   ├── vector/         # ChromaDB client and embedding helpers
│   │   │   ├── cache/          # Redis client with in-memory fallback, session state
│   │   │   └── schemas/        # Pydantic request/response models
│   │   ├── data/
│   │   │   ├── knowledge/      # Practice markdown (Tier 1) + API cache
│   │   │   └── chroma/         # ChromaDB persistent storage (gitignored)
│   │   ├── scripts/
│   │   │   ├── seed.py         # DB seeding (idempotent)
│   │   │   └── embed_knowledge.py  # Knowledge embedding pipeline
│   │   └── tests/
│   └── frontend/
│       ├── Dockerfile          # Multi-stage Node 20-alpine build
│       └── src/
│           ├── app/            # Next.js App Router pages
│           ├── components/     # React components (WelcomeScreen, ChatWindow, etc.)
│           └── lib/            # API client, types
└── Spec/                       # Design spec + assessment requirements
```
