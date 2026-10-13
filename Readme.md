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

### API endpoints

| Method and path | Purpose |
|---|---|
| `POST /research/run` | Start a run (multipart form: `brief`, `files` up to 10 PDFs of 20 MB, `include_web`, `include_documents`, `clarification`) |
| `GET /research/runs/{id}` | Run status, error, clarifying question, `report_id` |
| `GET /research/status/{id}` | Server-sent events for live progress (supports `Last-Event-ID`) |
| `GET/POST /watchlist`, `GET/PATCH/DELETE /watchlist/{id}` | Watchlist CRUD (changes sync the scheduler job) |
| `POST /watchlist/{id}/run` | Run a watchlist item now (202); diff and alert follow as for scheduled runs |
| `GET /alerts` (`unread_only`, `watchlist_item_id`, `limit`, `offset`), `POST /alerts/{id}/read`, `POST /alerts/read-all` | In-app alerts raised on material changes |
| `GET /reports`, `GET /reports/{id}` | List and retrieve reports |
| `GET /reports/{id}/export.md`, `export.pdf` | Downloads |
| `POST /reports/{id}/share`, `GET /share/{token}` | 7-day signed share link and its public read-only view |

**Watchlist monitoring.** On startup the API schedules every active watchlist item (daily or weekly) in-process, so run it with a single worker. Each run researches the topic, then the Diff Agent compares the new report with the previous one for that item (sentence matching with citations stripped; rephrasing with the same figures is ignored; an LLM judge drops leftover rephrasings). The first run is the baseline. A material change (a changed figure, an added or removed finding with figures, or 3+ added/removed findings) creates an in-app alert and, if the item has a `webhook_url`, POSTs a Slack-compatible `{"text": ...}` message (3 attempts with backoff; failures are logged, never fatal). Set `SCHEDULER_ENABLED=false` to turn the scheduler off.

Errors always use `{"error": {"code", "message", "details"?}}`. There is no user authentication (single-user capstone); set a long random `JWT_SECRET` in `.env` before sharing links. Interactive docs are at `/docs`.

### Backend layout

| Path | Purpose |
|---|---|
| `api/config.py` | Settings and Qdrant client factory |
| `api/llm.py` | LLM concurrency semaphore and 429 backoff (`call_llm`) |
| `api/schemas/` | Pydantic models for every agent boundary |
| `api/context/` | Scratch store, token budget (`fit_to_budget`), run log, session memory, summarizer |
| `api/agents/` | Planner, researcher runner, synthesis, writer, fact-checker |
| `api/graph/` | LangGraph pipeline and run/status event recorder |
| `api/routes/`, `api/services/` | HTTP endpoints (research, watchlist, reports, share) and their helpers (uploads, export, share tokens, run executor) |
| `api/db/` | SQLAlchemy models and session (SQLite, WAL mode) |
| `api/scheduler/` | APScheduler runner: one interval job per active watchlist item, then diff, alert and webhook |

## Sample briefs

Five example briefs with the expected report structure for each are in [samples/](samples/README.md).

## Running the frontend locally

```bash
cd app
npm install
npm run dev      # http://localhost:3000
npm run lint
npm run build
```

The frontend is Next.js (App Router, TypeScript, Tailwind CSS, `src/` layout). It talks to the API at `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`; set it in `app/.env.local`), so start the API first and make sure `CORS_ORIGINS` includes the frontend origin.

| Page | What it does |
|---|---|
| `/` | Brief panel: 500-character counter, PDF drag-and-drop (10 files, 20 MB each), web/document toggles, runtime estimate; recent reports |
| `/runs/[id]` | Live progress over SSE: task rows (Queued/Running/Done/Failed), agent activity log, progress bar, elapsed time; clarifying-question form |
| `/reports`, `/reports/[id]` | Report viewer: sticky table of contents, clickable citations opening a source drawer, amber `UNVERIFIED` marks, confidence badges, fact-check panel, PDF / `.md` export and 7-day share link |
| `/share/[token]` | Public read-only view of a shared report |
| `/watchlist` | Watched topics (add, pause, change frequency, run now, delete), one-click diff view, alert feed |

The citation drawer shows the cited source's title, date, credibility and the claims it supports (the report stores `sources` and `evidence` next to the text).
