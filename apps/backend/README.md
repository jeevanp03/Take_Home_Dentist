# Backend — FastAPI + Python 3.11+

The backend handles all business logic: ReAct agent orchestration, tool execution, database access, vector search, and session management. The frontend never talks to the DB or LLM directly — everything goes through this API.

## Quick Start (Local)

```bash
cd apps/backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# Seed database with sample data
python -m scripts.seed

# Embed practice knowledge into ChromaDB (required for RAG)
python -m scripts.embed_knowledge --practice-only

# Start the server
uvicorn src.main:app --reload --port 8000
```

## Quick Start (Docker)

From the project root:

```bash
# Build and start the backend + Redis
docker-compose up -d redis backend

# Run scripts inside the container
docker-compose exec backend python -m scripts.seed
docker-compose exec backend python -m scripts.embed_knowledge --practice-only
```

> **Note:** The backend Dockerfile doesn't exist yet — it will be created in a later phase. For now, use the local setup above.

## Verify

- Health: http://localhost:8000/api/health → `{"status": "ok"}`
- API docs: http://localhost:8000/docs

## Prerequisites

- Python 3.11+ (local) or Docker (containerized)
- Redis running on localhost:6379 (run `docker-compose up -d redis` from project root)
- `.env` file at project root with at minimum `GEMINI_API_KEY` set

## Environment Variables

Loaded from the project root `.env` file (not `apps/backend/.env`). See `.env.example` for all options.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GEMINI_API_KEY` | Yes | — | Google AI Studio API key for Gemini 2.0 Flash |
| `JWT_SECRET_KEY` | Yes (prod) | `change-me-...` | Secret for signing JWT tokens. App warns if default is used in non-debug mode |
| `DATABASE_URL` | No | `sqlite:///./data/dental.db` | SQLAlchemy connection string. SQLite for dev, Postgres for prod |
| `REDIS_URL` | No | `redis://localhost:6379` | Redis connection. Falls back to in-memory dict if unavailable |
| `CHROMA_PERSIST_DIR` | No | `./data/chroma` | Where ChromaDB stores vector embeddings on disk |
| `MAX_CONCURRENT_LLM_CALLS` | No | `10` | Semaphore limit for parallel Gemini API calls |
| `DEBUG` | No | `true` | Enables verbose logging |

## Scripts

All scripts run from `apps/backend/` with the virtualenv activated.

| Command | Docker equivalent | Description |
|---------|-------------------|-------------|
| `python -m scripts.seed` | `docker-compose exec backend python -m scripts.seed` | Seed DB with 5 patients, ~228 time slots (2 weeks, Mon-Sat), and 3 appointments. Idempotent. |
| `python -m scripts.embed_knowledge --practice-only` | `docker-compose exec backend python -m scripts.embed_knowledge --practice-only` | Embed 6 local practice markdown files into ChromaDB (35 chunks, no API calls). |
| `python -m scripts.embed_knowledge` | `docker-compose exec backend python -m scripts.embed_knowledge` | Full embed: practice + PubMed (20 topics) + MedlinePlus (19 topics). Caches API responses. |
| `python -m scripts.embed_knowledge --refresh` | `docker-compose exec backend python -m scripts.embed_knowledge --refresh` | Re-embed from cached API responses (no network). |
| `python -m scripts.embed_knowledge --repull` | `docker-compose exec backend python -m scripts.embed_knowledge --repull` | Re-fetch all APIs, update cache, re-embed. |

## Project Structure

```
src/
  main.py                   # FastAPI app, CORS, lifespan (DB init + Redis cleanup)
  config.py                 # Pydantic BaseSettings, loads .env from project root
  db/
    database.py             # SQLAlchemy engine, session factory, init_db(), get_db()
    models.py               # Patient, TimeSlot, Appointment, ConversationLog (SQLAlchemy 2.0)
    repositories.py         # PatientRepository, SlotRepository, AppointmentRepository
  agent/
    orchestrator.py         # ReAct loop with session mutex (Phase 2)
    system_prompt.py        # Mia persona + rules + few-shot examples (Phase 2)
    llm.py                  # Gemini client with semaphore + retry (Phase 2)
    date_parser.py          # Natural language → ISO date range (Phase 2)
    message_converter.py    # Redis dicts ↔ Gemini Content objects (Phase 2)
    tools/
      __init__.py           # Tool registry, execute_tool(), get_tool_declarations()
      knowledge.py          # search_knowledge_base (RAG with MMR)
      conversations.py      # search_past_conversations
      patients.py           # lookup_patient, create_patient
      appointments.py       # get_available_slots, book, reschedule, cancel
      notifications.py      # notify_staff
      practice_info.py      # get_practice_info (static, no vector search)
  vector/
    chroma_client.py        # Singleton PersistentClient, collection getters
  cache/
    redis_client.py         # Async Redis with connection pool + in-memory fallback
    session.py              # Session CRUD, TTL management, session locking
  api/
    routes.py               # POST /api/chat (SSE), GET /api/slots, GET /api/health
    auth.py                 # JWT create/verify (Phase 3)
    auth_routes.py          # POST /api/auth/token, /api/auth/refresh (Phase 3)
    debounce.py             # SMS-style message buffering (Phase 3)
  schemas/
    __init__.py             # Pydantic request/response models (Phase 2)
data/
  knowledge/                # 6 practice markdown files (office info, procedures, etc.)
    cache/                  # Cached PubMed/MedlinePlus API responses (gitignored)
  chroma/                   # ChromaDB persistent storage (gitignored)
  dental.db                 # SQLite database (gitignored)
scripts/
  seed.py                   # DB + slot + patient seeding
  embed_knowledge.py        # Knowledge embedding pipeline (practice + PubMed + MedlinePlus)
tests/
  conftest.py               # Test fixtures (Phase 2+)
```

## Database

SQLite with WAL mode for concurrent reads. Tables:

- **patients** — id, full_name, phone (unique), date_of_birth, insurance_name
- **time_slots** — id, date, start_time, end_time, is_available, provider_name. Composite index on (date, is_available)
- **appointments** — id, patient_id FK, slot_id FK, appointment_type, notes, status. Indexed on patient_id and status
- **conversation_logs** — id, session_id, patient_id FK, messages (JSON text), summary

All IDs are `uuid4().hex[:16]` strings.

## Knowledge Base

Three tiers embedded into ChromaDB's `dental_kb` collection:

1. **Practice markdown** (Tier 1) — 6 hand-authored files covering office info, insurance, procedures, emergency protocol, FAQ, family booking
2. **MedlinePlus** (Tier 2) — Patient-friendly summaries for 19 dental topics
3. **PubMed** (Tier 3) — Research abstracts for 20 dental topics (5-8 per topic)

Retrieval: top-5 from ChromaDB → MMR for diversity → top-3 with similarity threshold (reject cosine distance > 0.5). Practice docs prioritized over external sources.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```
