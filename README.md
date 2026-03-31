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
| LLM | Gemini 2.0 Flash via google-generativeai SDK |
| Agent | ReAct loop with 11 tools |

## Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- Docker (for Redis)

### 1. Clone and configure environment

```bash
git clone <repo-url> && cd <repo>
cp .env.example .env
cp apps/frontend/.env.local.example apps/frontend/.env.local
```

Edit `.env` — you only need to set two values:

| Variable | How to get it |
|----------|--------------|
| `GEMINI_API_KEY` | Free at [Google AI Studio](https://aistudio.google.com/apikey) — sign in, click "Create API Key" |
| `JWT_SECRET_KEY` | Generate with: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

Everything else is pre-filled with working defaults (SQLite, localhost Redis, etc.).

> **Note:** The frontend has its own env file (`apps/frontend/.env.local`) because Next.js only reads `NEXT_PUBLIC_*` vars from its own directory. The default `NEXT_PUBLIC_API_URL=http://localhost:8000` works out of the box.

### 2. Start Redis

```bash
docker-compose up -d redis
```

> The `docker-compose.yml` also defines `backend` and `frontend` services, but their Dockerfiles don't exist yet. For now, use the manual setup steps below. Running `docker-compose up -d redis` starts only Redis.

### 3. Backend

```bash
cd apps/backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# Seed the database with sample patients, time slots, and appointment types
python -m scripts.seed

# Embed practice knowledge into ChromaDB (required for RAG)
python -m scripts.embed_knowledge --practice-only

# Start the server
uvicorn src.main:app --reload --port 8000
```

### 4. Frontend

```bash
cd apps/frontend
npm install
npm run dev
```

### 5. Verify

- Backend health: http://localhost:8000/api/health
- API docs: http://localhost:8000/docs
- Frontend: http://localhost:3000

## Scripts

Run from `apps/backend/` with the virtualenv activated, or via Docker:

| Local (from `apps/backend/`) | Docker (from project root) | What it does |
|------------------------------|---------------------------|-------------|
| `python -m scripts.seed` | `docker-compose exec backend python -m scripts.seed` | Seed DB with sample data. Idempotent. |
| `python -m scripts.embed_knowledge --practice-only` | `docker-compose exec backend python -m scripts.embed_knowledge --practice-only` | Embed local practice markdown only (fast). **Run this first.** |
| `python -m scripts.embed_knowledge` | `docker-compose exec backend python -m scripts.embed_knowledge` | Embed all knowledge (practice + PubMed + MedlinePlus). |
| `python -m scripts.embed_knowledge --refresh` | `docker-compose exec backend python -m scripts.embed_knowledge --refresh` | Re-embed from cached API responses. |
| `python -m scripts.embed_knowledge --repull` | `docker-compose exec backend python -m scripts.embed_knowledge --repull` | Re-fetch APIs, update cache, re-embed. |

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
                                  │  │  - Gemini 2.0 Flash (temp=0.4)  │    │
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

### Key Components

- **Agent Pattern**: ReAct loop (not a rigid chain) — handles unpredictable conversation pivots. Max 5 tool-calling iterations per turn. Session-level mutex prevents concurrent runs for same session.
- **LLM**: Gemini 2.0 Flash via `google-generativeai` SDK. `temperature=0.4`, `top_p=0.9`. Safety settings: `BLOCK_ONLY_HIGH` for medical content.
- **Database**: SQLite with WAL mode for concurrent reads (dev). Postgres-ready for prod via SQLAlchemy 2.0.
- **Vector DB**: ChromaDB in-process with `PersistentClient`. Two collections: `dental_kb` (knowledge) and `conversations` (past chats). Uses default `all-MiniLM-L6-v2` embeddings (384 dims, runs locally, no API calls).
- **Cache**: Redis for hot conversation state (messages, intent, booking state) with 30-min TTL. In-memory dict fallback if Redis unavailable.
- **Auth**: Stateless JWT tokens (HS256), 1hr expiry, auto-refresh at 50 min.
- **Knowledge Base**: Three tiers — hand-authored practice markdown + MedlinePlus patient summaries + PubMed research abstracts.

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

### Design Decisions

- **Decoupled frontend/backend** — either can be swapped independently via HTTP + SSE
- **ChromaDB in-process** — no separate server needed; adequate for ~400 dental docs
- **Redis as buffer, not source of truth** — conversations flush to ChromaDB + SQLite on session end
- **Concurrency via async + semaphores**, not threads; SQLite WAL for concurrent reads
- **SMS-style debouncing** — 2-3s buffer concatenates rapid messages before dispatching to agent
- **Source-weighted retrieval** — practice docs prioritized over PubMed for office-specific questions
- **Retrieval pipeline** — top-5 from ChromaDB → MMR for diversity → return top-3 (reject cosine distance > 0.5)
- **Practice queries bypass vector search** — `get_practice_info` returns static data instantly

## Detailed Docs

- **[Backend README](apps/backend/README.md)** — env vars, scripts, DB schema, knowledge base, project structure
- **[Frontend README](apps/frontend/README.md)** — setup, commands, how it connects to the backend

## Project Structure

```
apps/
  backend/
    src/
      main.py               # FastAPI app, CORS, startup events
      config.py              # Pydantic BaseSettings
      db/                    # SQLAlchemy models, database setup, repositories
      agent/                 # ReAct orchestrator, system prompt, LLM client, tools
      vector/                # ChromaDB client and embedding helpers
      cache/                 # Redis client with in-memory fallback
      api/                   # Routes, JWT auth, debounce middleware
      schemas/               # Pydantic request/response models
    data/
      knowledge/             # Practice-specific markdown (Tier 1)
      chroma/                # ChromaDB persistent storage (gitignored)
    scripts/
      seed.py                # DB seeding
      embed_knowledge.py     # Knowledge embedding pipeline
    tests/                   # pytest test suite
  frontend/
    src/app/                 # Next.js App Router pages and components
```
