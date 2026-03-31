# Frontend — Next.js 16 + TypeScript + Tailwind CSS

Chat-only UI for the dental practice chatbot. All business logic lives in the backend — the frontend is a thin client that sends messages and renders responses via SSE streaming.

## Quick Start (Local)

```bash
cd apps/frontend
npm install
npm run dev
```

Open http://localhost:3000

## Quick Start (Docker)

From the project root:

```bash
docker-compose up -d frontend
```

> **Note:** The frontend Dockerfile doesn't exist yet — it will be created in a later phase. For now, use the local setup above.

## Prerequisites

- Node.js 18+
- Backend running on http://localhost:8000 (or set `NEXT_PUBLIC_API_URL`)

## Environment Variables

Stored in `apps/frontend/.env.local` (not the root `.env`). Next.js only reads `NEXT_PUBLIC_*` vars from its own directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend API base URL |

To set up: `cp .env.local.example .env.local`

## Available Commands

| Command | Description |
|---------|-------------|
| `npm run dev` | Start dev server on port 3000 with hot reload |
| `npm run build` | Production build |
| `npm run start` | Start production server |
| `npm run lint` | Run ESLint |

## Tech

- **Next.js 16** (App Router) with `src/` directory structure
- **React 19**
- **TypeScript 5**
- **Tailwind CSS 4** via PostCSS plugin

## Project Structure

```
src/
  app/
    layout.tsx              # Root layout (html + body + fonts)
    page.tsx                # Home page — currently shows backend health status
    globals.css             # Tailwind imports + CSS custom properties
public/                     # Static assets (favicon, etc.)
```

## How It Connects to the Backend

The frontend fetches `GET /api/health` on page load to verify backend connectivity. The health status is displayed with a color-coded indicator (green = connected, red = unreachable).

Once the chat UI is built (Phase 4+), messages will be sent via `POST /api/chat` and responses streamed back via SSE (Server-Sent Events).

## Notes

- The frontend has zero business logic — it never talks to the DB, Redis, or ChromaDB directly
- Authentication is handled via JWT tokens from the backend's `/api/auth/token` endpoint
- The chat UI will auto-refresh the JWT at 50 minutes (1hr expiry)
