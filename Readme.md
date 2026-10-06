# InsightForge AI: Autonomous Market Intelligence Engine

Multi-agent research and competitive analysis platform. A user submits a natural-language brief and receives a structured, cited intelligence report. Scheduled watchlist jobs monitor topics and alert on material changes.

## Project structure

| Folder | Purpose |
|---|---|
| `api/` | Backend (Python, FastAPI, LangGraph, DeepAgents) |
| `app/` | Frontend (Next.js) |
| `assignment/` | Original assignment brief (read-only reference, do not edit) |
| `planning/` | [Implementation plan](planning/plan.md) and [task tracker](planning/task.md) |

## Stack decisions

- LLM: Groq (configurable; Claude or GPT-4o can be swapped in)
- Web search: SerpAPI
- Vector DB: Qdrant server via `qdrant_client` and `QDRANT_URL` (runs in Docker Compose; a Qdrant Cloud URL also works)
- Database: SQLite (WAL mode)
- Python tooling: uv (`pyproject.toml` + `uv.lock`)
- Runtime: the whole stack (`qdrant`, `api`, `app`) runs via Docker Compose

## Status

Planning complete (plan v1.3). Implementation has started (Phase 0: setup). See [planning/task.md](planning/task.md).

Setup instructions, API keys, and the context-engineering writeup will be added as the build progresses.

## Running the API locally (without Docker)

```bash
cd api
uv sync                              # creates .venv and installs from uv.lock
uv run uvicorn main:app --reload    # http://localhost:8000/health
uv run pytest
```

Add dependencies with `uv add <package>`.

Docker Compose setup will be added in Phase 0 (tasks 0.4–0.8).
