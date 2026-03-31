---
name: sanity-check
description: Quick end-to-end smoke test — env vars, DB, API, frontend, agent loop. Answers "does it actually run?" Not a full test suite.
---

Run a quick smoke test across the entire stack. For each layer, check the minimum viable "does it work?"

1. **Environment** — .env exists with all keys, Python 3.11+, Node 18+, packages installed
2. **Database** — SQLite file exists, tables have correct schema, can read/write
3. **External services** — Redis reachable, Gemini API key valid, ChromaDB directory writable
4. **Backend** — FastAPI imports clean, server starts, health endpoint responds, /api/chat works
5. **Frontend** — builds without errors, dev server starts, main page loads
6. **Agent loop** — system prompt loads, tools registered, basic message completes a ReAct cycle

Read `.agents/sanity-check.md` for the full checklist. Stop at first critical failure per section. Report pass/fail with error details.
