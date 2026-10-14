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
| `POST /research/run` | Start a run (multipart form: `brief`, `files` and/or `document_urls` (links to PDFs) up to 10 documents in total of 20 MB each, `include_web`, `include_documents`, `clarification`) |
| `GET /research/runs/{id}` | Run status, error, clarifying question, `report_id` |
| `GET /research/status/{id}` | Server-sent events for live progress (supports `Last-Event-ID`) |
| `GET/POST /watchlist`, `GET/PATCH/DELETE /watchlist/{id}` | Watchlist CRUD (changes sync the scheduler job) |
| `POST /watchlist/{id}/run` | Run a watchlist item now (202); diff and alert follow as for scheduled runs |
| `GET /alerts` (`unread_only`, `watchlist_item_id`, `limit`, `offset`), `POST /alerts/{id}/read`, `POST /alerts/read-all` | In-app alerts raised on material changes |
| `GET /reports`, `GET /reports/{id}` | List and retrieve reports |
| `GET /reports/{id}/export.md`, `export.pdf` | Downloads |
| `POST /reports/{id}/share`, `GET /share/{token}` | 7-day signed share link and its public read-only view |

**Watchlist monitoring.** On startup the API schedules every active watchlist item (daily or weekly) in-process, so run it with a single worker. Each run researches the topic, then the Diff Agent compares the new report with the previous one for that item (sentence matching with citations stripped; rephrasing with the same figures is ignored; an LLM judge drops leftover rephrasings). The first run is the baseline. A material change (a changed figure, an added or removed finding with figures, or 3+ added/removed findings) creates an in-app alert and, if the item has a `webhook_url`, POSTs a Slack-compatible `{"text": ...}` message (3 attempts with backoff; failures are logged, never fatal). Set `SCHEDULER_ENABLED=false` to turn the scheduler off.

**PDF links.** Instead of downloading a PDF and uploading it, paste its link. The server downloads it when the run starts and stores it with the uploads, so it is handled exactly like an uploaded file. Links must be `http(s)`, point directly at a PDF and be publicly reachable; links to private or local addresses are refused (including through redirects). A bad link rejects the whole request and starts no run.

Errors always use `{"error": {"code", "message", "details"?}}`. There is no user authentication (single-user capstone); set a long random `JWT_SECRET` in `.env` before sharing links. Interactive docs are at `/docs`.

### Researchers

Each sub-task goes to one researcher, which sees only that sub-task (never the brief or other sub-tasks) and returns references, not content:

| Researcher | What it does |
|---|---|
| Web search | SerpAPI top 5, full article fetch and parse (trafilatura), credibility score (domain tier, recency decay, byline), skips and logs paywalls (HTTP 401/402/403 or teaser text) and login walls; stores bodies in the scratch store (trimmed to `SOURCE_MAX_CHARS`) |
| Document reader | Docling extraction and structural chunking of the run's PDFs (falls back to pypdf if Docling cannot run), indexed in Qdrant `doc_chunks` with source file, page number and section heading; top-8 retrieval per sub-task; citations use the file name |
| Entity extractor | Searches like the web researcher, then extracts companies, people, products, funding, figures, dates and regions with relationships into Qdrant `entities`; two sources disagreeing on a fact sets `conflict: true` on the entity. Best effort: if extraction fails, the fetched sources still count as evidence |

Researchers call no LLM except entity extraction, which keeps research cheap on free tiers. Page fetches refuse private and local addresses. The first Docling run downloads its layout models (a few minutes, one time).

**Free-tier models (Groq).** Limits apply per model, so synthesis (the most calls) runs on `qwen/qwen3.8-27b` and the other roles on `openai/gpt-oss-120b`. `openai/gpt-oss-20b` was tested and cannot do structured output here (it answers in plain text), so do not assign it to a role. Roles are set with `PLANNER_MODEL`, `SYNTHESIS_MODEL`, `WRITER_MODEL`, `ENTITY_MODEL`; an empty value uses `LLM_MODEL`. Rate-limit errors are retried using the wait time Groq reports.

Check the real researchers with `uv run python scripts/phase2_smoke_test.py [web|docs|entities|all]` (uses one search and one small LLM call).

### Backend layout

| Path | Purpose |
|---|---|
| `api/config.py` | Settings and Qdrant client factory |
| `api/llm.py` | LLM concurrency semaphore and 429 backoff (`call_llm`) |
| `api/schemas/` | Pydantic models for every agent boundary |
| `api/context/` | Scratch store, token budget (`fit_to_budget`), run log, session memory, summarizer |
| `api/agents/` | Planner, researchers (`web_search`, `doc_reader`, `entity_extractor`), synthesis, writer, fact-checker, diff |
| `api/tools/` | Researcher building blocks: SerpAPI search, article fetch and parse with paywall detection, credibility scoring, Docling PDF chunking |
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
| `/` | Brief panel: 500-character counter, PDF drag-and-drop or links to PDFs (10 documents, 20 MB each), web/document toggles, runtime estimate; recent reports |
| `/runs/[id]` | Live progress over SSE: task rows (Queued/Running/Done/Failed), agent activity log, progress bar, elapsed time; clarifying-question form |
| `/reports`, `/reports/[id]` | Report viewer: sticky table of contents, clickable citations opening a source drawer, amber `UNVERIFIED` marks, confidence badges, fact-check panel, PDF / `.md` export and 7-day share link |
| `/share/[token]` | Public read-only view of a shared report |
| `/watchlist` | Watched topics (add, pause, change frequency, run now, delete), one-click diff view, alert feed |

The citation drawer shows the cited source's title, date, credibility and the claims it supports (the report stores `sources` and `evidence` next to the text).
