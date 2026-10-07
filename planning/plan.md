# Implementation Plan: InsightForge AI Market Intelligence Engine

**Version:** 1.5
**Source:** [assignment_04_market_intelligence.md](../assignment/assignment_04_market_intelligence.md)
**Task tracking:** [task.md](task.md)

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-10-06 | Initial approved plan. Decisions: Groq LLM, SerpAPI, embedded `qdrant_client`, SQLite. |
| 1.1 | 2026-10-06 | Qdrant switched from embedded local mode to server mode via `QDRANT_URL` (Docker Compose for local dev, matches prior projects; Qdrant Cloud also works). Added per-role model config, LLM concurrency limit, Groq smoke test as first task, SQLite WAL mode. Removed the embedded single-process risk. |
| 1.2 | 2026-10-06 | Whole project containerized: Docker Compose runs `qdrant`, `api`, and `app`. Added Dockerfiles, `.dockerignore`, healthchecks, and container networking. Task tracking moved from `todo_task.md` to `task.md` (status + datetime log). |
| 1.3 | 2026-10-06 | Python tooling switched from `pip`/`venv`/`requirements.txt` to **uv** with `api/pyproject.toml` + `api/uv.lock`. Docker build uses uv with `--frozen` for reproducible, cached installs. |
| 1.4 | 2026-10-07 | Added CI/CD with GitHub Actions (`.github/workflows/ci.yml`): API tests (uv + pytest) and app lint/build on every push and PR to `main`. Docker build check and Qdrant-backed integration tests to be added as Docker and Qdrant code lands. |
| 1.5 | 2026-10-07 | **Docker dropped** (Docker unusable on the dev laptop). Project runs locally: `uv run uvicorn` for the API, `npm run dev` for the app. Qdrant reverts to **embedded local mode** (`QDRANT_PATH`, default `./data/qdrant`) with optional `QDRANT_URL` for Qdrant Cloud or a standalone server. Removed Dockerfiles, `.dockerignore`, `docker-compose.yml` and the Docker CI job; tasks 0.4 to 0.8, 0.17 and 9.8 cancelled. |

> Any change to this plan is made by bumping the version above and adding a changelog row. Do not silently edit past decisions.

---

## 1. Decisions

| Area | Decision | Notes |
|---|---|---|
| LLM | **Groq** via `langchain-groq` (default) | Provider and model set by env vars (`LLM_PROVIDER`, `LLM_MODEL`); Claude or GPT-4o can be swapped in without code changes. |
| Web search | **SerpAPI** | Provider kept behind an interface (`SEARCH_PROVIDER`) so Tavily/Brave can be added later. |
| Vector DB | **Qdrant via `qdrant_client`**: embedded local mode by default (`QDRANT_PATH`, default `./data/qdrant`); `QDRANT_URL` (+ optional `QDRANT_API_KEY`) switches to Qdrant Cloud or a standalone server | No Docker needed. Embedded mode allows one process at a time, so the API runs as a single worker (the scheduler already runs in-process). A Qdrant Cloud URL works with no code change if a separate process is ever needed. |
| Runtime | **Local processes, no Docker**: `api` (uvicorn) and `app` (`next dev`) | Persistent data under `./data/` (Qdrant, SQLite, scratch store, model cache). Details in section 3a. |
| Relational DB | SQLite (via SQLAlchemy), **WAL mode on** | Runs, task graphs, reports, watchlist, alerts, session summaries. Fine for a single-user capstone; move to Postgres only for multi-user or multi-worker deployment (connection-string change). |
| Model config | Per-role models: `PLANNER_MODEL`, `RESEARCHER_MODEL`, `SYNTHESIS_MODEL`, `WRITER_MODEL`, `FACT_CHECK_MODEL` | All default to Groq. Writer can be switched to Claude/GPT if the grounding check keeps failing. |
| LLM concurrency | Semaphore (`LLM_MAX_CONCURRENCY`, default 3) + exponential backoff on 429 | Keeps parallel researchers under Groq rate limits. |
| Embeddings | Local sentence-transformers model (configurable) | Used for Qdrant chunks, entities, and fact-check similarity. |
| Backend | FastAPI + `sse-starlette` | In `api/`. |
| Python tooling | **uv** (`pyproject.toml` + `uv.lock`) | `uv sync`, `uv run`, `uv add`. Same lockfile locally and in CI. |
| Frontend | Next.js 14+ App Router | In `app/`. |
| Orchestration | LangGraph state machine + `deepagents` Planner | |
| Scheduler | APScheduler | |
| PDF export | WeasyPrint (fallback: Playwright) | |
| CI/CD | **GitHub Actions** | Workflow `.github/workflows/ci.yml` runs on push and PR to `main`. See section 3b. |

**Groq caveat:** the free tier has rate and token limits, so parallel researchers use the concurrency semaphore plus retry/backoff, and the 6,000-token evidence budget helps keep prompts small. The chosen model must support tool calling, which `deepagents` requires. A smoke test (one `deepagents` agent making one `task` subagent call) is the first task in Phase 0, so problems surface on day one.

**SQLite caveat:** enable WAL (`PRAGMA journal_mode=WAL`) and keep write transactions short (especially SSE status updates from parallel agents) to avoid "database is locked" errors when scheduled jobs write while the UI reads.

---

## 2. Target repository layout

```
market_intelligence_engine/
├── api/                      # FastAPI backend (Python)
│   ├── main.py
│   ├── config.py             # env-driven settings, no hardcoded keys
│   ├── schemas/              # Pydantic models for every agent boundary
│   ├── agents/               # planner, web_search, doc_reader, entity_extractor,
│   │                         # synthesis, writer, fact_checker, diff
│   ├── context/              # scratch_store, token_budget, memory_store
│   ├── graph/                # LangGraph state machine
│   ├── scheduler/            # APScheduler + watchlist runner
│   ├── routes/               # research, watchlist, reports
│   ├── db/                   # SQLAlchemy models + session
│   ├── tests/
│   ├── pyproject.toml
│   └── uv.lock
├── app/                      # Next.js frontend
├── data/                     # runtime data (gitignored): qdrant/ (embedded), sqlite, scratch/, model cache
├── .github/workflows/ci.yml  # CI pipeline
├── assignment/               # original assignment brief (read-only)
├── planning/                 # plan.md, task.md
├── samples/                  # 5 sample briefs + expected structure
├── evaluation/               # eval scripts + report
├── .env.example
├── .gitignore
└── Readme.md
```

---

## 3. Phases

### Phase 0: Setup and foundations
- **First task: Groq smoke test.** Confirm the chosen Groq model handles tool calling in a `deepagents` agent with one `task` subagent call. If flaky, pick another model before building anything.
- Scaffold `api/` (Python 3.11+, uv, `pyproject.toml` + `uv.lock`) and `app/` (`create-next-app`, TypeScript, App Router).
- `.env.example` with: `GROQ_API_KEY`, `LLM_PROVIDER`, per-role `*_MODEL` vars, `LLM_MAX_CONCURRENCY`, `SERPAPI_API_KEY`, `SEARCH_PROVIDER`, `QDRANT_PATH` (embedded, default), `QDRANT_URL` and `QDRANT_API_KEY` (optional, Cloud or standalone), `DATABASE_URL`, `EVIDENCE_TOKEN_BUDGET`, `MAX_PARALLEL_SUBTASKS`, `JWT_SECRET`, `LANGSMITH_*` (optional).
- `config.py` using `pydantic-settings`, including a Qdrant client factory (`QDRANT_URL` if set, else embedded at `QDRANT_PATH`).
- Pydantic schemas for every agent boundary: `ResearchBrief`, `SubTask`, `TaskGraph`, `SourceRecord`, `SourceRef`, `EntityGraph`, `EvidenceItem`, `EvidenceBundle`, `Report`, `FactCheckSummary`, `DiffSummary`, `RunState`.
- SQLite models: `Run`, `TaskGraphLog`, `Report`, `WatchlistItem`, `Alert`, `SessionSummary`. WAL mode enabled at connection time.
- Update `.gitignore` for `data/`, SQLite files, and local Qdrant storage.
- CI pipeline with GitHub Actions (see section 3b).

#### 3a. Local run design

| Process | Command | Port | Notes |
|---|---|---|---|
| `api` | `cd api && uv run uvicorn main:app --reload` | 8000 | Single worker (embedded Qdrant lock, in-process scheduler). Reads settings from `.env`. Data under `./data/`. |
| `app` | `cd app && npm run dev` | 3000 | `NEXT_PUBLIC_API_URL=http://localhost:8000`. |

- **Qdrant:** embedded by default, stored at `QDRANT_PATH` (`./data/qdrant`). Set `QDRANT_URL` to use Qdrant Cloud or a standalone server instead. Collections (`doc_chunks`, `entities`) behave the same in both modes.
- **Secrets:** read from `.env`; `.env` is gitignored and `.env.example` is committed.
- **WeasyPrint on Windows:** needs GTK/Pango runtime libraries. If installing them is painful, use the Playwright fallback for PDF export.
- **Torch:** install CPU-only wheels via a `[[tool.uv.index]]` PyTorch CPU index when torch is added, to avoid a large CUDA download.
- **Single API process:** run one worker so scheduled jobs don't fire twice and the embedded Qdrant lock isn't contended.

#### 3b. CI/CD (GitHub Actions)

Workflow: `.github/workflows/ci.yml`, triggered on push and pull request to `main`, with concurrency cancellation of superseded runs and read-only permissions.

| Job | Steps |
|---|---|
| `api` | checkout, install uv (cached on `api/uv.lock`), `uv python install 3.12`, `uv sync --frozen`, `uv run pytest -q` |
| `app` | checkout, Node 22 with npm cache, `npm ci`, `npm run lint`, `npm run build` |

Planned additions, added when the pieces exist:
- **Integration tests:** tests that need the vector DB use embedded Qdrant in a temp directory (Phase 2), so CI needs no service container.
- **Secrets:** tests must not call Groq or SerpAPI. LLM and search calls are mocked, so CI needs no API keys. Any live-API evaluation runs manually, not in CI.
- Optionally make the `CI` check required on `main` via branch protection once it is stable.

### Phase 1: Context engineering layer (build first)
- **Scratch store:** keyed store (`write_source` returns `source_id`; `read_source(source_id)`), backed by files under `data/scratch/` or the DeepAgents virtual filesystem. Raw content never lives only in conversation history.
- **Token budget:** `tiktoken`-style counter plus `fit_to_budget(items, budget)`. Over budget means compress lower-priority items by summarizing, never arbitrary truncation. Each cut is written to the run log with the reason.
- **Memory store:** load only the last 3 compressed session summaries at planner start. Auto-summarization enabled for long sessions.
- **Run log:** structured per-run log capturing token counts at each boundary. This is the evidence for the README's context-blowout example.

### Phase 2: Researcher agents
- **Web Search Agent:** SerpAPI top 5, fetch and parse the full article body, credibility score (domain tier, recency decay, byline), detect 402/login walls and skip with logging. Writes `{url, title, published_date, body_text, credibility_score}` to scratch; returns only `source_id` plus a one-line summary.
- **Document Reader Agent:** up to 10 PDFs, Docling extraction and semantic chunking, Qdrant collection `doc_chunks` with `{source_filename, page_number, section_heading}`, top-k=8 retrieval, return chunk IDs only.
- **Entity Extractor Agent:** LLM-based extraction (companies, people, products, funding, dollar figures, dates, regions), entity graph JSON, Qdrant collection `entities`, `conflict: true` on disagreeing facts.

### Phase 3: Planner and orchestration
- Planner via `create_deep_agent()` with `write_todos`. Output is a typed `TaskGraph` with 3–6 sub-tasks (cap from `MAX_PARALLEL_SUBTASKS`, max 6).
- Vague-brief detection: ask exactly one clarifying question before proceeding.
- Researchers spawned as isolated subagents via the `task` tool. Each prompt contains only its sub-task, scope, and its tool definitions. Results come back as references.
- LangGraph graph: `PLANNING → RESEARCHING (parallel) → SYNTHESIZING → WRITING → FACT_CHECKING → COMPLETE`, with typed state at every transition, a join barrier before synthesis, and failure handling per branch.
- Task graph and status events persisted to the DB for the live progress view.

### Phase 4: Synthesis, Writer, Fact-Checker
- **Synthesis:** resolves refs from scratch and Qdrant (the only agent allowed to pull raw content), dedupes by entity and claim, assigns confidence (HIGH: 2+ independent sources, MEDIUM: single high-credibility, LOW: single low-credibility or conflicting), ranks by recency then credibility then relevance, and enforces the token budget.
- **Writer:** report order is Executive Summary (≤150 words), thematic sections from the task graph, inline `[Source: {domain}, {date}, credibility: {score}]`, confidence label per section, Gaps & limitations. A grounding check verifies every claim traces to the bundle, with retry plus an explicit constraint on violation.
- **Fact-Checker:** sample 20% of claims (minimum 5), re-fetch the source, cosine similarity ≥ 0.85 via Qdrant, mark failures `[UNVERIFIED]`, emit `{total_checked, passed, failed, pass_rate, unverified_claims}`.

### Phase 5: FastAPI backend
- `POST /research/run`, `GET /research/status/{run_id}` (SSE), `/watchlist` CRUD, `/reports` list and retrieve, export (`.md`, PDF), and a JWT-signed 7-day share link endpoint.
- PDF upload endpoint (≤10 files, ≤20 MB each).
- Consistent error handling, CORS for the frontend, no hardcoded secrets.

### Phase 6: Scheduler and Diff Agent
- APScheduler job per watchlist item (daily/weekly) running the same pipeline. Persist `last_run` and `next_run`. Runs in the FastAPI process (`AsyncIOScheduler`) for simplicity.
- Diff Agent compares the new report with the stored previous one, outputs `{added_findings, removed_findings, changed_findings}`, and filters out rephrasing.
- In-app alert on material change. Webhook POST (Slack-compatible) when `webhook_url` is set.

### Phase 7: Next.js frontend
- Brief input panel: 500-char limit and counter, drag-and-drop PDFs, source toggles, runtime estimate.
- Live progress: SSE-driven sub-task rows (Queued/Running/Done/Failed), agent activity log, progress bar, elapsed time.
- Report viewer: Markdown rendering, sticky table of contents, clickable citations opening a side drawer (title, date, credibility, source paragraph), amber `[UNVERIFIED]` with tooltip, green/amber/red confidence badges, fact-check summary panel.
- Watchlist dashboard: topics table, one-click diff view, alert feed.
- Export: PDF, `.md`, copy share link.

### Phase 8: Evaluation and deliverables
- 5 sample briefs across different industries and question types, each with expected report structure, in `samples/`.
- Evaluation script and report: fact-check pass rate (target ≥ 80%), citation accuracy, synthesis quality across the 5 briefs.
- README: setup, required API keys, env var reference, architecture diagram, context-engineering writeup with a real blowout example from run logs, a sample report, and one end-to-end walkthrough from brief to exported PDF.
- Demo video (5–7 minutes) and LinkedIn post. Submission repo name: `insightforge-agent`.

### Phase 9: Bonus (priority order)
1. LangSmith tracing
2. Adaptive context budgets
3. Citation trace
4. Contradiction resolution UI
5. Entity graph visualizer
6. Source credibility tuning
7. Multi-language research

---

## 4. Build order and rationale

Phases 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. Multi-agent architecture and context engineering are 40% of the grade and each must score ≥ 60%, so the context layer and typed schemas come before any agent. The backend is exercised through the API before the UI is built, so the UI has stable contracts.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Groq rate limits during parallel research | Concurrency semaphore, retry with backoff, small token budget, configurable per-role models |
| Groq model weak at tool calling or long outputs | Smoke test first in Phase 0; per-role model config lets us move the Writer to Claude/GPT |
| Embedded Qdrant allows one process only | Single API worker; tests use temp dirs; `QDRANT_URL` (Cloud) as the escape hatch |
| Heavy install / slow first sync (Docling, torch) | CPU-only torch index, lazy imports, `uv.lock` for reproducible installs, model cache under `data/` |
| WeasyPrint system libs missing on Windows | Playwright fallback for PDF export |
| SQLite "database is locked" | WAL mode, short write transactions |
| Paywalled or unparseable pages | Skip and log; request top-N+buffer results |
| Docling install size and speed | Lazy import; only used on PDF upload |
| Fact-check false negatives from threshold | Log similarity scores; tune on sample briefs |

## 6. Definition of done

All assignment deliverables 1–8 complete; passing thresholds met (overall ≥ 70%, Multi-agent architecture ≥ 60%, Context engineering ≥ 60%); fact-check pass rate ≥ 80% on the sample briefs.
