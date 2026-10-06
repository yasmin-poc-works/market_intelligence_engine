# InsightForge AI: Autonomous Market Intelligence Engine

Multi-agent research and competitive analysis platform. A user submits a natural-language brief and receives a structured, cited intelligence report. Scheduled watchlist jobs monitor topics and alert on material changes.

## Project structure

| Folder | Purpose |
|---|---|
| `api/` | Backend (Python, FastAPI, LangGraph, DeepAgents) |
| `app/` | Frontend (Next.js) |
| `planning/` | Assignment brief, [implementation plan](planning/plan.md), and [task tracker](planning/task.md) |

## Stack decisions

- LLM: Groq (configurable; Claude or GPT-4o can be swapped in)
- Web search: SerpAPI
- Vector DB: Qdrant server via `qdrant_client` and `QDRANT_URL` (runs in Docker Compose; a Qdrant Cloud URL also works)
- Database: SQLite (WAL mode)
- Runtime: the whole stack (`qdrant`, `api`, `app`) runs via Docker Compose

## Status

Planning complete (plan v1.2). Implementation has not started. See [planning/task.md](planning/task.md).

Setup instructions, API keys, and the context-engineering writeup will be added as the build progresses.
