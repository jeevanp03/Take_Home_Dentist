---
name: test-backend
description: Test the FastAPI backend — API endpoints, DB operations, agent integration, and external services. Use after building or modifying backend code.
---

Test the FastAPI backend systematically:

1. **API endpoints** — POST /api/chat (SSE streaming), GET /api/slots, auth flow
2. **Database** — repository CRUD, constraints, indexed query performance
3. **Agent integration** — system prompt loads, tools registered, basic conversation completes
4. **External services** — Redis read/write, ChromaDB search, Gemini API call

Read `.agents/test-backend.md` for the full test plan. Run existing tests first (`pytest`), then manual endpoint tests with `curl` or `httpx`.

Report pass/fail per category with error details and suggested fixes.
