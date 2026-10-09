# InsightForge AI: Autonomous Market Intelligence Engine

Multi-agent research and competitive analysis platform. A user submits a natural-language brief and receives a structured, cited intelligence report. Scheduled watchlist jobs monitor topics and alert on material changes.

## Project structure

| Folder | Purpose |
|---|---|
| `api/` | Backend (Python, FastAPI, LangGraph, DeepAgents) |
| `app/` | Frontend (Next.js, TypeScript, Tailwind) |
| `assignment/` | Original assignment brief (read-only reference, do not edit) |
| `planning/` | [Implementation plan](planning/plan.md) and [task tracker](planning/task.md) |

## Stack decisions

- LLM: Groq (configurable; Claude or GPT-4o can be swapped in)
- Web search: SerpAPI
- Vector DB: Qdrant via `qdrant_client`, embedded local mode by default (`QDRANT_PATH`); set `QDRANT_URL` to use Qdrant Cloud or a standalone server
- Database: SQLite (WAL mode)
- Python tooling: uv (`pyproject.toml` + `uv.lock`)
- Runtime: runs locally, no Docker (`api` with uvicorn, `app` with `next dev`)

## Status

Planning complete (plan v1.5). Implementation has started (Phase 0: setup). See [planning/task.md](planning/task.md).

Setup instructions, API keys, and the context-engineering writeup will be added as the build progresses.

## Running the API locally

```bash
cd api
uv sync                              # creates .venv and installs from uv.lock
uv run uvicorn main:app --reload    # http://localhost:8000/health
uv run pytest
```

Add dependencies with `uv add <package>`.

Qdrant runs embedded inside the API process (data in `data/qdrant`), so there is nothing else to start. Run a single API worker.

### Configuration

```bash
cp .env.example .env     # repo root; fill in GROQ_API_KEY and SERPAPI_API_KEY
```

Settings are loaded by `api/config.py` (pydantic-settings). Relative paths (`QDRANT_PATH`, `DATABASE_URL`, `SCRATCH_PATH`) resolve against the repo root, and all runtime data lands in the gitignored `data/` folder. Set `QDRANT_URL` (and `QDRANT_API_KEY`) to use Qdrant Cloud or a standalone server instead of embedded storage. Per-role models (`PLANNER_MODEL`, `WRITER_MODEL`, ...) fall back to `LLM_MODEL` when empty.

### Backend layout

| Path | Purpose |
|---|---|
| `api/config.py` | Settings and Qdrant client factory |
| `api/llm.py` | LLM concurrency semaphore and 429 backoff (`call_llm`) |
| `api/schemas/` | Pydantic models for every agent boundary |
| `api/context/` | Scratch store, token budget (`fit_to_budget`), run log, session memory, summarizer |
| `api/agents/` | Planner, researcher runner, synthesis, writer, fact-checker |
| `api/graph/` | LangGraph pipeline and run/status event recorder |
| `api/db/` | SQLAlchemy models and session (SQLite, WAL mode) |
| `api/routes/`, `api/scheduler/` | Planned modules (see task tracker) |

## Running the frontend locally

```bash
cd app
npm install
npm run dev      # http://localhost:3000
npm run lint
npm run build
```

The frontend is Next.js (App Router, TypeScript, Tailwind CSS, `src/` layout).
