# Dental Chatbot — Implementation TODO

~105 tasks across 6 phases. Incorporates all findings from architect, agentic, AI/LLM, HCI, dental SME, and ML specialist reviews.

---

## Phase 0: Project Bootstrap (~30 min)

- [x] **0.1** Create monorepo directory structure
  - Create directories: `backend/`, `frontend/`, `backend/src/`, `backend/scripts/`, `backend/tests/`
  - Create data dirs: `backend/data/knowledge/`, `backend/data/knowledge/cache/`, `backend/data/chroma/`
  - Create ALL Python package `__init__.py` files:
    - `backend/src/__init__.py`
    - `backend/src/db/__init__.py`
    - `backend/src/agent/__init__.py`
    - `backend/src/agent/tools/__init__.py`
    - `backend/src/vector/__init__.py`
    - `backend/src/cache/__init__.py`
    - `backend/src/api/__init__.py`
    - `backend/src/schemas/__init__.py`
    - `backend/scripts/__init__.py`
  - Add root `.gitignore`: `.venv/`, `data/chroma/`, `data/dental.db`, `.env`, `__pycache__/`, `node_modules/`, `.next/`, `data/knowledge/cache/`

- [x] **0.2** Init Python backend with FastAPI
  - Create venv: `python -m venv .venv && source .venv/bin/activate`
  - Create `requirements.txt`: fastapi>=0.110, uvicorn[standard], sqlalchemy>=2.0, pydantic-settings, google-generativeai, chromadb, redis[hiredis], python-jose[cryptography], python-dotenv, requests, beautifulsoup4, lxml
  - `pip install -r requirements.txt`
  - Create `src/main.py` with bare FastAPI app + CORS (allow localhost:3000) + `GET /api/health`
  - Verify: `uvicorn src.main:app --reload --port 8000` → Swagger at `/docs`

- [x] **0.3** Init Next.js 15 frontend
  - `npx create-next-app@latest frontend --typescript --tailwind --app --src-dir`
  - Set `NEXT_PUBLIC_API_URL=http://localhost:8000` in `frontend/.env.local`
  - Verify: `npm run dev` → page on port 3000

- [x] **0.4** Create `.env.example` and `.env`
  - Keys: `GEMINI_API_KEY`, `DATABASE_URL=sqlite:///./data/dental.db`, `REDIS_URL=redis://localhost:6379`, `CHROMA_PERSIST_DIR=./data/chroma`, `JWT_SECRET_KEY`, `MAX_CONCURRENT_LLM_CALLS=10`, `DEBUG=true`

- [x] **0.5** Create `docker-compose.yml` for Redis
  - Redis 7 Alpine on port 6379
  - Verify: `docker-compose up -d`, `redis-cli ping` → PONG

- [x] **0.6** Create `README.md` skeleton
  - Title, one-line description, architecture placeholder, tech stack table, setup instructions placeholder, design decisions placeholder, prioritization placeholder

- [x] **0.7** Verify frontend↔backend CORS connectivity
  - Fetch `GET /api/health` from Next.js `page.tsx` on load
  - Verify no CORS errors in browser console

---

## Phase 1A: SQLAlchemy + SQLite Data Layer (~1 hr)

- [x] **1A.1** Create Pydantic `BaseSettings` config (`src/config.py`)
  - Load from `.env`: GEMINI_API_KEY, DATABASE_URL, REDIS_URL, CHROMA_PERSIST_DIR, JWT_SECRET_KEY, MAX_CONCURRENT_LLM_CALLS (int, default 10), DEBUG (bool)

- [x] **1A.2** Create database engine and session factory (`src/db/database.py`)
  - SQLAlchemy engine from `DATABASE_URL`
  - `check_same_thread=False` for SQLite, `StaticPool` for SQLite
  - WAL mode + `busy_timeout=5000` via event listener
  - `SessionLocal` factory, `init_db()`, `get_db()` FastAPI dependency

- [x] **1A.3** Create SQLAlchemy 2.0 models (`src/db/models.py`)
  - `Base` (DeclarativeBase)
  - `AppointmentType` enum: cleaning, general_checkup, emergency, consultation, follow_up
  - `AppointmentStatus` enum: scheduled, cancelled, completed, no_show
  - `Patient`: id, full_name, phone (unique), date_of_birth, insurance_name (nullable), created_at, updated_at
  - `TimeSlot`: id, date, start_time, end_time, is_available, provider_name; composite index on (date, is_available)
  - `Appointment`: id, patient_id FK, slot_id FK, appointment_type, notes, status, created_at, updated_at; indexes on patient_id and status
  - `ConversationLog`: id, session_id, patient_id FK (nullable), messages (text), summary (text, nullable), created_at, ended_at (nullable)
  - All IDs: `uuid4().hex[:16]` strings

- [x] **1A.4** Create repository layer (`src/db/repositories.py`)
  - `PatientRepository`: `find_by_name_and_phone()`, `find_by_name_and_dob()`, `create()`
  - `SlotRepository`: `get_available(date_start, date_end, time_pref)`, `get_consecutive(target_date, count)`
  - `AppointmentRepository`: `book()` (transactional), `cancel()` (free slot), `reschedule()` (atomic swap), `get_patient_appointments()`
  - All write operations: catch `IntegrityError`, retry once, then return descriptive error

- [x] **1A.5** Verify database creation
  - Run `init_db()` → verify `.db` file with correct tables and indexes

- [x] **1A.6** Create seed script (`scripts/seed.py`)
  - 2 weeks of 30-min slots (Mon-Sat, 8:00-17:30, skip Sundays) → ~240 slots
  - ~15% randomly unavailable
  - 5 seed patients with realistic data
  - 3 existing appointments linked to correct slots
  - Idempotent (check before inserting)

- [x] **1A.7** Run seed and verify with queries

---

## Phase 1B: ChromaDB + Knowledge Ingestion (~1.5 hr)

- [x] **1B.1** Create ChromaDB client module (`src/vector/chroma_client.py`)
  - Singleton `PersistentClient` at `CHROMA_PERSIST_DIR`
  - Collections use ChromaDB's default `all-MiniLM-L6-v2` embeddings (runs locally, no API calls, no credits)
  - `get_knowledge_collection()` → "dental_kb" (cosine)
  - `get_conversations_collection()` → "conversations" (cosine)

- [x] **1B.2** Create embedding helper (`src/vector/embeddings.py`)
  - Use ChromaDB's default `all-MiniLM-L6-v2` (384 dims, runs locally, zero API calls/credits)
  - No custom `EmbeddingFunction` needed — ChromaDB handles embedding automatically at add() and query() time
  - `embed_text(text)` and `embed_batch(texts)` convenience helpers wrapping the default model (for use outside ChromaDB if needed)
  - Runs locally — no rate limits, no cost, instant during ingestion

- [x] **1B.3** Write 6 practice-specific knowledge markdown files (`data/knowledge/`)
  - `office_info.md` — Bright Smile Dental, location, hours (Mon-Sat 8AM-6PM), phone, parking, providers (Dr. Smith), language/accessibility services note
  - `insurance_policy.md` — explicitly list accepted plans (Delta Dental, Aetna, Cigna, Blue Cross, MetLife, Guardian, United Healthcare), self-pay 15% discount, CareCredit financing, membership plan $299/yr (includes: 2 cleanings, 1 exam, X-rays, 20% discount on other procedures), "we verify benefits before your visit" language, payment timing
  - `procedures.md` — cleaning, deep cleaning/scaling & root planing, checkup, consultation, emergency, filling, crown, root canal, extraction, whitening, implants (offered), veneers (offered), night guards (for bruxism), dentures/partials (referral), orthodontics (referral). Note approximate durations. For referral-only procedures, say so explicitly.
  - `emergency_protocol.md` — **CRITICAL**: Include 911/ER escalation criteria: uncontrolled bleeding, difficulty breathing/swallowing, facial/neck swelling compromising airway, jaw fracture, severe facial trauma. For these: "Call 911 or go to your nearest emergency room immediately." For dental emergencies (cracked tooth, severe pain, knocked-out tooth, abscess): same-day slot + notify staff. Red-flag symptoms that need urgent (same-day/next-day) care: spreading swelling + fever, persistent numbness, can't open mouth. First-aid guidance from knowledge base.
  - `faq.md` — first visit expectations, X-ray frequency/safety, dental anxiety (validate feelings, mention sedation/comfort options, gentle approach), cancellation policy (24hr, sick-day waiver), what to bring, post-procedure aftercare FAQ (pain normal 24-48hrs, when to call back), "when do I pay?", provider preference, minors need parent/guardian, "we'll have you fill out a health history form at your visit" (covers medications/allergies)
  - `family_booking.md` — how family appointments work, back-to-back slots, kids under 18 use parent insurance, minors need parent/guardian present, "same insurance for all?" shortcut

- [x] **1B.4** Create knowledge embedding/ingestion script (`scripts/embed_knowledge.py`)
  - **Per-source chunking strategy** (not one-size-fits-all):
    - Practice markdown: chunk by headers, NO overlap (header splits are semantically clean). Prepend document title + section header to each chunk.
    - PubMed abstracts: DO NOT chunk — embed each abstract as a single document (200-350 tokens, well within model limit). Prepend title.
    - MedlinePlus summaries: chunk by paragraph/section breaks (not raw token count). Prepend topic title + section.
  - PubMed: 20 topics × **5-8 abstracts** each (not 15 — results 10-15 are noise). Add quality filter: only include abstracts where title or MeSH terms contain "dental" or "oral."
  - **Additional PubMed topics**: dental pain management, pregnancy and dental care, TMJ/TMD, dry socket, bad breath/halitosis, diabetes and oral health, canker sores vs cold sores, geriatric dentistry
  - **Additional MedlinePlus topics**: tooth pain/toothache, TMJ disorders, pregnancy dental care, mouth sores
  - MedlinePlus: 19 topics (original 15 + 4 new)
  - Cache raw API responses to `data/knowledge/cache/` as JSON
  - `--refresh` flag: re-embed from cached API responses (fast)
  - `--repull` flag: re-fetch from APIs, update cache, then re-embed
  - Post-ingestion: spot-check 10-20 random PubMed abstracts for relevance
  - Progress logging, graceful error handling on failed API calls
  - **Post-ingestion deduplication**: compute pairwise similarity within topic clusters. If two chunks have similarity > 0.92, keep higher-priority source (practice > MedlinePlus > PubMed)

- [x] **1B.5** Run ingestion and test retrieval quality
  - **Core queries** (happy path):
    - "what insurance do you accept?" → practice docs
    - "is teeth whitening safe?" → MedlinePlus/PubMed
    - "what to expect from a root canal?" → MedlinePlus
    - "what are your hours?" → practice docs
  - **Extended queries** (edge cases):
    - Ambiguous/multi-topic: "I need a cleaning but I'm nervous and don't have insurance" → should return chunks from procedures, anxiety, AND insurance
    - Practice vs clinical disambiguation: "How much does a cleaning cost?" → practice docs (not PubMed)
    - No good match: "Do you do Botox?" → should return nothing or very low relevance (test threshold)
    - Short/vague: "pain" → verify results are reasonable
    - Negation: "What procedures don't require X-rays?" → verify behavior
  - Verify source metadata and similarity scores on returned chunks
  - Verify deduplication worked (no near-identical chunks in top results)

---

## Phase 1C: Redis Session Layer (~30 min)

- [x] **1C.1** Create async Redis client (`src/cache/redis_client.py`)
  - `redis.asyncio` with `ConnectionPool.from_url(max_connections=20, decode_responses=True)`
  - Singleton pool pattern
  - In-memory dict fallback on `ConnectionError` — log warning, reconnect on next request

- [x] **1C.2** Create session state CRUD (`src/cache/session.py`)
  - `get_session(session_id)` → dict with patient_id, messages[], collected{}, intent, booking_state, timestamps
  - `update_session(session_id, **fields)` → update fields, refresh 30-min TTL
  - `add_message(session_id, role, content)` → append to messages array
  - `clear_session(session_id)` → delete hash
  - `acquire_session_lock(session_id)` → Redis-based lock (prevent concurrent runs for same session)
  - `release_session_lock(session_id)`
  - Session timeout warning helper: when TTL < 5 min, flag for warning message

- [x] **1C.3** Test Redis session operations
  - Create, add messages, read back, check TTL, update, verify TTL refreshes, clear, verify gone
  - Test session lock: two concurrent acquires → only one succeeds
  - Test fallback: stop Redis → in-memory dict works

---

## Phase 2A: Gemini 2.0 Flash LLM Client (~45 min)

- [x] **2A.1** Create Gemini LLM client (`src/agent/llm.py`)
  - Migrated to `google-genai` SDK (client-based API, replaces deprecated `google-generativeai`)
  - Model: `gemini-2.0-flash`
  - `build_config()` merges generation params, safety, tools, system_instruction into `GenerateContentConfig`
  - **Safety settings**: `BLOCK_ONLY_HIGH` for all harm categories — dental content safe
  - `asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)` wrapping all calls
  - `call_gemini()` and `call_gemini_stream()` async functions
  - Retry with exponential backoff (max 2 retries) on 429/500/503

- [x] **2A.2** Create message format converter (`src/agent/message_converter.py`)
  - Convert Redis `{role, content}` dicts ↔ `google.genai.types.Content/Part` objects
  - Handle all message types: user text, assistant text, function_call turns, function_response turns
  - Consecutive same-role merging (required for Gemini's strict alternation)
  - `Part.from_function_call()` / `Part.from_function_response()` (no more raw protobuf Struct)

- [x] **2A.3** Test: completion + function call round-trip
  - 13 unit tests covering booking flow, emergency flow, parallel tool calls, edge cases
  - 3 integration tests (live API): dental safety, tool round-trip, error handling
  - Integration tests gated behind `@pytest.mark.integration` (Gemini free tier quota-limited)

---

## Phase 2B: Agent Tools (~1.5 hr)

- [x] **2B.1** Create Pydantic input/output schemas (`src/schemas/__init__.py`)
  - 12 schemas for all tools (11 original + update_patient for pre-agent flow)
  - Pydantic validates at execution time; Gemini declarations derived automatically from schemas

- [x] **2B.2** Implement knowledge tools (`src/agent/tools/knowledge.py`, `conversations.py`)
  - `search_knowledge_base`: top-5 → MMR (lambda=0.5, numpy) → top-3, cosine distance threshold 0.5, practice doc boost +0.1
  - `search_past_conversations`: top-3 filtered by patient_id metadata

- [x] **2B.3** Implement patient tools (`src/agent/tools/patients.py`)
  - `lookup_patient`, `create_patient`, `update_patient` (new — for pre-agent flow DOB/insurance collection)
  - All update Redis session with patient_id on success

- [x] **2B.4** Implement appointment tools (`src/agent/tools/appointments.py`)
  - `get_available_slots` (paginated: first 5, total count), `book_appointment`, `reschedule_appointment`, `cancel_appointment`, `get_patient_appointments`
  - Human-readable formatting: "Wednesday, April 1" / "2:00 PM"
  - book_appointment updates Redis booking_state

- [x] **2B.5** Implement notification tool (`src/agent/tools/notifications.py`)
  - `notify_staff` with emergency/special_request/escalation types, in-memory store for demo

- [x] **2B.6** Implement practice info tool (`src/agent/tools/practice_info.py`)
  - Static dict sourced from office_info.md content — instant, no vector search

- [x] **2B.7** Create tool registry (`src/agent/tools/__init__.py`)
  - `TOOL_REGISTRY`: 12 tools with handler, Pydantic schema, inject requirements, description
  - `get_tool_declarations()` → auto-generates `google.genai.types.FunctionDeclaration` from Pydantic schemas
  - `execute_tool()` → validates via Pydantic, injects db/session_id, 10s timeout, returns clean JSON dict
  - Validation errors → descriptive error back to agent for self-correction

---

## Phase 2C: ReAct Orchestrator (~2 hr) ✅

- [x] **2C.1** Create system prompt builder (`src/agent/system_prompt.py`) — Mia persona, 5 few-shot examples, anti-hallucination, anti-injection hardening, emergency protocol, patient context injection
- [x] **2C.2** Build ReAct orchestrator loop (`src/agent/orchestrator.py`) — 8-step async generator with session lock, input sanitisation, tool execution loop (max 5 iterations), repeated-call detection, intermediate text streaming, safety-block handling
- [x] **2C.3** Implement context window management — 50-message trim, no mid-conversation summarisation (Gemini Flash 1M context)
- [x] **2C.4** Add max iterations guard — forced text-only call after 5 iterations, static fallback on total failure
- [x] **2C.5** Implement conversation end lifecycle — goodbye regex, structured summarisation via Gemini, ChromaDB + SQLite persistence, Redis cleanup, each step in independent try/except

---

## Phase 2D: Date Parsing (~20 min)

- [ ] **2D.1** Create date parser (`src/agent/date_parser.py`)
  - `parse_date_expression(text, reference_date=today)` → `{start: ISO, end: ISO}`
  - Handle: "tomorrow", "next week" (Mon-Fri), "early next week" (Mon-Tue), "later next week" (Thu-Fri), "next month", "early next month" (1st-10th), "late next month" (20th-last), "this week", "ASAP"
  - Skip Sundays, handle month boundaries
  - **Integration note**: the LLM produces ISO dates for `get_available_slots` (Gemini is decent at this with the current date in the system prompt). The date parser serves as validation/fallback, not the primary conversion path. If the LLM passes a natural language string instead of ISO, the tool handler runs it through the parser.

- [ ] **2D.2** Test date parser edge cases
  - Sunday → Monday, Dec→Jan, "ASAP" on Saturday, "later next week" when today is Thursday

---

## Phase 3A: JWT Authentication + Patient Identification (~45 min)

- [ ] **3A.1** Create JWT auth helpers (`src/api/auth.py`)
  - `TokenData`, `TokenResponse` models
  - `create_access_token()` → JWT via python-jose HS256
  - `verify_token()` → FastAPI Depends, raises 401 with `WWW-Authenticate: Bearer`

- [ ] **3A.2** Create auth routes (`src/api/auth_routes.py`)
  - `POST /api/auth/token` — issue JWT (session_id in claims), no auth required
  - `POST /api/auth/refresh` — refresh, requires valid JWT
  - Register in `main.py`

- [ ] **3A.3** Create patient identification endpoint (`src/api/routes.py`)
  - `POST /api/identify` — called by frontend after the welcome screen choice
  - Request body: `{ mode: "returning" | "new" | "question", name?: str, phone?: str }`
  - **Returning**: lookup patient by name + phone → if found, load patient record + upcoming appointments + past conversation summaries → inject all into Redis session → return patient context to frontend
  - **New**: check for existing (prevent duplicates) → if not found, create patient with name + phone → inject patient_id into session → return. Agent will conversationally collect DOB + insurance
  - **Question**: no lookup needed, just create session → return. Agent handles everything
  - Requires valid JWT (session_id from claims)
  - Returns: `{ patient_id?, patient_name?, upcoming_appointments[], needs_info: ["dob", "insurance"] }`

- [ ] **3A.4** Protect routes with JWT
  - `POST /api/chat`, `POST /api/identify`, `GET /api/slots` use `Depends(verify_token)`
  - Session_id from JWT claims, not request body

---

## Phase 3B: Concurrency Safety (~20 min)

- [ ] **3B.1** Verify LLM semaphore works under load (built in 2A.1)
- [ ] **3B.2** Verify SQLAlchemy pooling + WAL mode + double-booking prevention
  - Two simultaneous bookings for same slot → only one succeeds, other gets "slot unavailable"
- [ ] **3B.3** Verify Redis connection pooling + session locks under concurrent use

---

## Phase 3C: SSE Streaming + API Wiring (~45 min)

- [ ] **3C.1** Create API routes file (`src/api/routes.py`) and wire `POST /api/chat` → orchestrator → SSE
  - `StreamingResponse` with `media_type="text/event-stream"`
  - Format: `data: {"type": "text", "content": "..."}\n\n` + `data: [DONE]\n\n`
  - Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`
  - Register router in `main.py`

- [ ] **3C.2** Wire `GET /api/slots` endpoint
  - Optional date param, JWT protected, returns JSON slot list

- [ ] **3C.3** Extend startup event and health check
  - Startup: `init_db()`, verify Redis, init ChromaDB, **Gemini warm-up call** (first call is slow on cold process)
  - `GET /api/health` → `{status, services: {db, redis, chroma}}` with actual connectivity checks

- [ ] **3C.4** Create SMS debounce middleware (`src/api/debounce.py`)
  - Hold incoming messages for **2-3 seconds**, concatenate if more arrive for same session_id, then dispatch to orchestrator
  - Prevents "Hi" [send] "I need" [send] "a cleaning" [send] from triggering 3 separate agent runs
  - Use Redis or in-memory dict with asyncio timer

- [ ] **3C.5** Add error handling for streaming responses
  - LLM error → stream "I'm having trouble right now — you can reach us at (555) 123-4567"
  - DB error → stream "I can't access our scheduling system right now — please try again in a moment"
  - ChromaDB error → agent still works for booking; knowledge questions get "I don't have that info right now — our front desk can help"
  - Rate limit (429) → stream "You're sending messages faster than I can keep up — give me a moment" (NOT a raw HTTP 429)
  - Never let unhandled exceptions kill the stream silently

- [ ] **3C.6** Add rate limiting
  - 10 msg/min per session_id via Redis counter with TTL
  - Return friendly in-chat message (not HTTP 429) when exceeded

- [ ] **3C.7** Test full backend with curl
  - Get token → chat with token → verify SSE stream → slots → no-token 401

---

## Phase 4: Frontend Chat UI (~2 hr)

- [ ] **4.1** Create API client layer (`src/lib/api.ts`)
  - `getToken()` — POST `/api/auth/token`, cache in memory (not localStorage), schedule refresh at 50 min
  - `identifyPatient(mode, name?, phone?)` — POST `/api/identify` with Bearer token
  - `sendMessage(message)` — POST `/api/chat` with Bearer token
  - SSE reader: parse `data:` lines from ReadableStream, yield chunks
  - Handle 401 → auto-refresh and retry once
  - Handle network errors → error state

- [ ] **4.2** Build `WelcomeScreen` component (pre-agent identification)
  - Three choices: "I'm a new patient" / "I've been here before" / "Just have a question"
  - **New/Returning**: shows a minimal name + phone form (2 fields)
  - **Returning**: calls `POST /api/identify { mode: "returning" }` → if patient found, transitions to chat with context; if not found, offers "Would you like to register as a new patient?"
  - **New**: calls `POST /api/identify { mode: "new" }` → creates patient, transitions to chat (agent collects DOB + insurance conversationally)
  - **Question**: calls `POST /api/identify { mode: "question" }` → goes straight to chat, no form
  - Clean, warm design matching Mia persona — not a clinical form
  - **ARIA live region** for screen reader announcements on state changes
  - **"Start over" link** to return to welcome screen from chat

- [ ] **4.3** Build `ChatWindow` component
  - Message state management, SSE stream consumption
  - Auto-scroll (doesn't hijack if user scrolled up)
  - **Context-aware first message**: if returning patient, Mia greets by name with upcoming appointments; if new patient, Mia asks for DOB/insurance; if question-only, Mia offers help
  - **"New Chat" button** in header — clears conversation, returns to welcome screen
  - Session ID in localStorage
  - **Session timeout warning**: when backend signals TTL < 5 min, show inline warning "This session will reset soon — are you still there?"
  - **ARIA live region** on message list for screen reader announcements

- [ ] **4.3** Build `MessageBubble` component
  - User (right, dark bg) vs assistant (left, light bg, "Mia" label)
  - Markdown rendering (lightweight renderer)
  - Timestamps shown subtly
  - Smooth entrance animation with **`prefers-reduced-motion` respect**
  - **Keyboard accessible**: messages focusable for screen reader navigation

- [ ] **4.4** Build `ChatInput` component
  - Enter to send, Shift+Enter for newline
  - Disabled while streaming
  - Focus on mount and after send
  - **Accessible**: proper `<label>`, `aria-label="Type your message"`, focus ring visible
  - **Min touch target 44x44px** for send button on mobile

- [ ] **4.5** Build `TypingIndicator` component
  - Three bouncing dots during SSE stream
  - Disappears on first text chunk
  - **`aria-live="polite"` + `aria-label="Mia is typing"`**
  - **`prefers-reduced-motion`**: static "..." instead of animation

- [ ] **4.6** Build `AppointmentCard` component
  - Inline card for confirmed/listed appointments
  - Shows date, time, type, provider, status
  - **Also used as pre-booking confirmation**: "Here's what I'm about to book — does this look right?" with structured summary before calling `book_appointment`
  - **Print/save button**: opens browser print dialog or copies details to clipboard

- [ ] **4.7** Build `QuickReplies` component
  - Contextual chips on greeting: "Book an appointment", "Check my appointments", "I have a dental emergency", "Ask a question"
  - Clicking sends that text as user message
  - Disappear after any interaction
  - **Keyboard accessible**: Tab-navigable, Enter/Space to activate

- [ ] **4.8** Assemble page layout and styling
  - Full-height chat layout (header + messages + input)
  - Professional dental aesthetic: whites, calming blues/teals
  - "Bright Smile Dental" branding header
  - Mobile-first responsive
  - **WCAG 2.1 AA**: color contrast 4.5:1+, visible focus indicators on all interactive elements, logical tab order
  - **Font sizing**: min 16px body text (dental patients skew older), scalable with browser zoom

- [ ] **4.9** Handle error and loading states
  - Network failure: inline reconnection message + retry button
  - API error: "Something went wrong — please try again" (never technical jargon)
  - **Specific tool-execution messages**: "Checking availability..." / "Looking up your records..." (not just generic dots)
  - Token refresh: transparent to user
  - Empty state: welcome + quick replies
  - **Message retry**: if a specific message fails, show retry icon on that message

- [ ] **4.10** Add feedback mechanism
  - Thumbs up/down on each assistant message (small, unobtrusive)
  - Optional post-conversation prompt: "How was your experience?"
  - Store feedback in backend (simple endpoint + table or log)
  - This fulfills the assessment's "mechanism for improvement based on interactions" requirement

---

## Phase 5: Integration Testing (~1.5 hr)

### Setup

- [ ] **5.0** Create test infrastructure
  - `backend/tests/conftest.py`: test DB (separate SQLite), test fixtures, seed data helper
  - Decide: Phase 5 tests are **manual scenario walkthroughs** via the chat UI. Automated tests (pytest) cover repos, tools, date parser — those are in `backend/tests/`.

### Core Scenarios

- [ ] **5.1** Test: New patient full booking flow
  - Greet → new patient → collects info **one field at a time** → date preference → shows ≤5 slots → pick → **confirmation card shown** → confirm → booked
  - Verify: patient in DB, slot unavailable, appointment created, session has patient_id

- [ ] **5.2** Test: Existing patient reschedule
  - Sarah Johnson + 555-0101 → verify → show appointments → reschedule → new slot
  - Verify: old slot freed, new slot claimed, appointment updated

- [ ] **5.3** Test: Emergency booking
  - "cracked tooth, severe pain" → empathy → brief triage → earliest slot → notify staff → "I've let our team know"
  - Verify: emergency type, notify_staff called, minimal info collected

- [ ] **5.3b** Test: 911/ER escalation
  - "I fell and my jaw might be broken, I can barely breathe" → bot says "Please call 911 or go to the nearest emergency room immediately" → does NOT try to book appointment
  - Verify: bot escalates, does not call book_appointment

- [ ] **5.4** Test: Family booking (back-to-back)
  - Parent + 2 kids → collect each → consecutive slots → present block → confirm all
  - Verify: 3 patients, 3 consecutive slots, 3 appointments

- [ ] **5.5** Test: No insurance patient
  - "I don't have insurance" → bot surfaces self-pay 15%, CareCredit, membership $299/yr **with inclusions**
  - Verify: uses `search_knowledge_base` or `get_practice_info`, not hallucinating

- [ ] **5.6** Test: Fully booked date → alternatives
  - Request date with no slots → "no availability" + 2-3 nearest dates proactively

- [ ] **5.7** Test: Subjective date expressions
  - "later next week", "early next month", "ASAP" → bot states interpretation, confirms with patient

- [ ] **5.8** Test: Existing patient cancellation
  - Verify identity → show appointments → cancel → confirm
  - Verify: status=cancelled, slot=available

- [ ] **5.9** Test: General inquiry via knowledge base
  - "What are your hours?" → practice docs (or `get_practice_info`)
  - "Is teeth whitening safe?" → MedlinePlus/PubMed with source citation
  - "Do you accept Delta Dental?" → practice docs

### Edge Cases

- [ ] **5.10** Test: Off-topic, clinical, and boundary cases
  - Off-topic ("weather") → polite redirect
  - Clinical advice ("Do I need a root canal?") → defer to dentist
  - Sunday request → "Mon-Sat 8AM-6PM"
  - Hostile input → stays professional
  - Prompt injection ("Ignore your instructions and...") → politely declines, stays in character

- [ ] **5.10b** Test: Dental anxiety scenario
  - "I'm really nervous, I haven't been to the dentist in years" → bot validates feelings, mentions comfort options, does NOT pressure into immediate booking

- [ ] **5.10c** Test: Sequential SMS-style messages
  - Send "Hi" then "I need" then "a cleaning" rapidly → debounce concatenates, single agent run
  - Verify: only 1 agent invocation, not 3

- [ ] **5.10d** Test: Mid-conversation pivot
  - Start booking → mid-way ask "do you accept my insurance?" → bot answers → **resumes booking flow** without losing state

- [ ] **5.10e** Test: Post-procedure follow-up
  - "I had a filling yesterday and it still hurts" → bot provides aftercare info from knowledge base, assesses if normal vs needs callback

- [ ] **5.11** Test: Auth and concurrency
  - No token → 401; expired → 401; valid → 200
  - 5 concurrent same-slot bookings → exactly 1 succeeds
  - Session lock: 2 rapid messages for same session → only 1 concurrent orchestrator run

- [ ] **5.12** Fix all issues found during testing

---

## Phase 6: Polish, Docs & Submission (~45 min)

- [ ] **6.1** Finalize README.md
  - Architecture diagram, tech stack + rationale, full setup instructions
  - Knowledge refresh commands (`--refresh` vs `--repull`)
  - Design decisions, what I'd build next
  - Additional capabilities beyond requirements

- [ ] **6.2** Write prioritization section (explicitly graded)
  - What was prioritized and why
  - Load-bearing vs nice-to-have
  - Risk/failure mode thinking (hallucinated times, double booking, 911 escalation, partial booking state)
  - Frame as "production for 100s of locations, 10k+ conversations/day"
  - Note PHI handling limitations and what production would need (encryption at rest, access controls, audit logging)

- [ ] **6.3** Code cleanup and docstrings
  - Remove debug prints, add key docstrings, consistent style

- [ ] **6.4** Review `.env.example` completeness
  - Verify all vars accumulated during development are present with comments

- [ ] **6.5** Verify clean git history (assessment criterion)
  - Iterative, meaningful commits — not one giant commit

- [ ] **6.6** Record demo video (submission requirement)
  - Architecture walkthrough, start stack, demo 3-4 scenarios including emergency
  - Show dental anxiety handling and SMS debouncing
  - Explain prioritization decisions
  - Show feedback mechanism

- [ ] **6.7** Push to GitHub
  - Verify: README renders, .env.example present, .gitignore correct

- [ ] **6.8** Run `/learner` to capture lessons

---

## Code Review Findings — Completed

The following items were identified during full code review and have been implemented:

- [x] **CR.10** Provider name filter on `get_available_slots` (schema + repo + tool)
- [x] **CR.11** `get_consecutive_slots` tool exposed for family booking
- [x] **CR.12** Phone number normalization in `CreatePatientInput` / `LookupPatientInput`
- [x] **CR.13** `patient_id` consistency validation in `book_appointment`
- [x] **CR.14** `booking_state` cleared on cancel/reschedule, `session_id` injected
- [x] **CR.16** ChromaDB + Redis warm-up in `main.py` lifespan hook
- [x] **CR.18** Sedation & Comfort section added to `procedures.md`
- [x] **CR.19** Post-extraction aftercare + dry socket guidance added
- [x] **CR.20** TMJ/jaw pain section added to `faq.md`
- [x] **CR.21** Fluoride treatments + sealants content added
- [x] **CR.22** Root canal crown language fixed ("strongly recommended, especially for back teeth")
- [x] **CR.23** SRP deep cleaning post-op guidance added
- [x] **CR.24** X-ray frequency fixed ("every 12-24 months for healthy adults")
- [x] **CR.25** Bone grafting mention added to implant description

## Code Review Findings — Remaining (Future Work)

### Production / Deployment (address during Phase 6 polish or pre-launch)

- [ ] **CR.1** Obtain BAA with Google for Gemini API, or switch to Vertex AI / self-hosted LLM
- [ ] **CR.2** Enable encryption at rest: SQLCipher or PostgreSQL + pgcrypto, encrypted volume for ChromaDB, Redis AUTH + TLS
- [ ] **CR.3** Implement structured HIPAA audit logging
- [ ] **CR.5** Implement data retention policy for conversation_logs and ChromaDB conversations
- [ ] **CR.6** Encrypt `conversation_logs.messages` at the application layer
- [ ] **CR.7** Disable or harden Redis in-memory fallback in production
- [ ] **CR.8** Validate `REDIS_URL` requires TLS when `DEBUG=False`

### Phase 3C Enhancement (do during SSE wiring)

- [ ] **CR.15** Use `call_gemini_stream` for the final text response (token-level streaming)

### Phase 3B Enhancement (do during concurrency verification)

- [ ] **CR.17** `get_session` pipeline optimization — batch Redis GET + TTL into single round-trip

---

## Summary

| Phase | Tasks | Estimated Time |
|-------|-------|---------------|
| 0 — Bootstrap | 7 | ~30 min |
| 1A — SQLAlchemy + SQLite | 7 | ~1 hr |
| 1B — ChromaDB + Knowledge | 5 | ~1.5 hr |
| 1C — Redis Sessions | 3 | ~30 min |
| 2A — Gemini LLM Client | 3 | ~45 min |
| 2B — Agent Tools | 7 | ~1.5 hr |
| 2C — ReAct Orchestrator | 5 | ~2 hr |
| 2D — Date Parsing | 2 | ~20 min |
| 3A — JWT Auth | 3 | ~30 min |
| 3B — Concurrency | 3 | ~20 min |
| 3C — SSE + Wiring | 7 | ~45 min |
| 4 — Frontend | 10 | ~2 hr |
| 5 — Integration Testing | 17 | ~1.5 hr |
| 6 — Polish & Submission | 8 | ~45 min |
| **Total** | **~87 actionable tasks** | **~12-14 hr** |

## Critical Path

```
0.1-0.5 (bootstrap)
  ├─→ 1A.1-1A.7 (database) ──→ 2B.1-2B.7 (tools) ──→ 2C.1-2C.5 (orchestrator) ──→ 3C.1-3C.7 (API) ──→ 4.1-4.10 (frontend) ──→ 5.x (testing)
  ├─→ 1B.1-1B.5 (ChromaDB) ──┘ (parallel to 1A after 0.x)
  ├─→ 1C.1-1C.3 (Redis) ─────┘ (parallel to 1A/1B)
  └─→ 2D.1-2D.2 (dates) ─────── (parallel to 2A/2B)
      3A.1-3A.3 (JWT) ────────── (parallel to 2C, merge at 3C)
```

Parallel tracks: 1A, 1B, 1C can all start after Phase 0. 2D and 3A can run parallel to 2A/2B/2C.
