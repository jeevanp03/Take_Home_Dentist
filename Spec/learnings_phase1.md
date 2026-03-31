# Phase 1 — Learnings

## What went well
- Parallel agent execution for 1A, 1B, 1C cut total time significantly — all three sub-phases are independent and can be built concurrently
- SQLAlchemy 2.0 `mapped_column()` style worked cleanly — no legacy 1.x quirks
- ChromaDB's built-in all-MiniLM-L6-v2 handles embedding at add/query time automatically, so no need to manage embedding vectors manually
- Practice markdown chunking by ## headers produces clean, semantically meaningful chunks (35 total from 6 files)
- Retrieval quality on practice-only is solid — correct chunks for insurance, hours, emergency, and children queries

## Bugs encountered and fixed
1. **ChromaDB `delete_collection` error type**: Newer ChromaDB versions throw `chromadb.errors.NotFoundError` instead of `ValueError`. Fix: catch broad `Exception` in `reset_collections()`.
2. **sentence-transformers not installed**: The dedup step called `embed_batch()` from `embeddings.py` which imports sentence-transformers — a separate library from ChromaDB's built-in ONNX model. Fix: query embeddings directly from ChromaDB via `collection.get(ids=..., include=["embeddings"])` instead of re-embedding with sentence-transformers.
3. **numpy truthiness on array**: `if not embeddings` fails for numpy arrays with >1 element. Fix: use `if embeddings is None or len(embeddings) == 0`.

## Architecture notes
- **dental.db** stores operational data: patients, time slots, appointments, conversation logs. It's the structured data layer, not user accounts (no auth table yet — JWT is stateless).
- **ChromaDB** stores vector embeddings for RAG: dental knowledge (practice docs, PubMed, MedlinePlus) and conversation summaries.
- **Redis** caches hot conversation state (messages, intent, booking state) with 30-min TTL. In-memory fallback works when Redis is down.
- The ONNX embedding model (~79MB) downloads on first ChromaDB use and caches at `~/.cache/chroma/onnx_models/`.

## Design decisions made
- Config uses `Path(__file__).resolve().parents[2] / ".env"` to find the .env at project root regardless of working directory
- Seed script uses seeded RNG (`random.seed(42)`) for deterministic data — same slots/patients every run
- Embed script supports `--practice-only` flag for fast iteration during dev without hitting external APIs
- Dedup pulls embeddings from ChromaDB rather than computing them again — avoids needing sentence-transformers as a dependency

## What to improve next time
- Consider installing sentence-transformers anyway if standalone embedding use cases arise outside ChromaDB
- The embed script's MedlinePlus API parsing needs real-world testing — the NLM web search API XML format may vary
- Docker-compose now includes backend + frontend services but Dockerfiles don't exist yet — need to create those
- 1C.3 (Redis session tests) marked complete but manual testing wasn't run in this session — should verify with actual Redis
