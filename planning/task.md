# Task Tracker: InsightForge AI Market Intelligence Engine

**Based on:** [plan.md](plan.md) v1.5
**Assignment:** [assignment_04_market_intelligence.md](../assignment/assignment_04_market_intelligence.md)

## How to use this file

- **Status values:** `TODO` → `IN PROGRESS` → `COMPLETED`, or `CANCELLED` when a task is dropped by a plan change (ID kept so history stays traceable)
- When you start a task: set Status to `IN PROGRESS` and fill **Started** (`YYYY-MM-DD HH:MM`).
- When you finish: set Status to `COMPLETED` and fill **Completed**.
- Leave Started/Completed empty until they happen. Add a note in the Notes column for blockers or decisions.
- If the plan version changes, update the version above and add or adjust tasks.

## Progress summary

| Phase | Total | TODO | IN PROGRESS | COMPLETED | CANCELLED |
|---|---|---|---|---|---|
| 0. Setup and foundations | 17 | 0 | 0 | 11 | 6 |
| 1. Context engineering layer | 5 | 0 | 0 | 5 | 0 |
| 2. Researcher agents | 9 | 9 | 0 | 0 | 0 |
| 3. Planner and orchestration | 6 | 6 | 0 | 0 | 0 |
| 4. Synthesis, Writer, Fact-Checker | 6 | 6 | 0 | 0 | 0 |
| 5. FastAPI backend | 7 | 7 | 0 | 0 | 0 |
| 6. Scheduler and Diff Agent | 4 | 4 | 0 | 0 | 0 |
| 7. Next.js frontend | 6 | 6 | 0 | 0 | 0 |
| 8. Evaluation and deliverables | 7 | 7 | 0 | 0 | 0 |
| 9. Bonus (optional) | 8 | 7 | 0 | 0 | 1 |

*Update these counts when statuses change.*

---

## Phase 0: Setup and foundations

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 0.1 | Groq smoke test: `deepagents` agent with one `task` subagent call (tool calling works) | COMPLETED | 2026-10-09 01:24 | 2026-10-09 01:30 | `openai/gpt-oss-120b` passed: one `task` subagent call, correct answer. Script: `api/scripts/groq_smoke_test.py` (manual, not CI) |
| 0.2 | Scaffold `api/` (uv, `pyproject.toml` + `uv.lock`, FastAPI skeleton) | COMPLETED | 2026-10-06 17:51 | 2026-10-06 17:53 | Local venv uses Python 3.12; health test passes. Migrated pip to uv on 2026-10-06 23:29 (plan v1.3) |
| 0.3 | Scaffold `app/` (Next.js 14+, TypeScript, App Router) | COMPLETED | 2026-10-06 23:39 | 2026-10-06 23:45 | Next.js 16.3.8, React 19, Tailwind 4, src/ dir, npm. Lint and build pass. `npm audit` reports 5 high issues in dev lint tooling (braces via eslint-config-next); not forced |
| 0.4 | `api/Dockerfile` (python:3.11-slim, uv with `uv sync --frozen`, WeasyPrint system libs, CPU-only torch) | CANCELLED | | 2026-10-07 14:59 | Docker dropped (plan v1.5): project runs locally |
| 0.5 | `app/Dockerfile` (node:20-alpine, dev and multi-stage prod) | CANCELLED | | 2026-10-07 14:59 | Docker dropped (plan v1.5): project runs locally |
| 0.6 | `.dockerignore` files for `api/` and `app/` | CANCELLED | | 2026-10-07 14:59 | Docker dropped (plan v1.5): project runs locally |
| 0.7 | `docker-compose.yml`: `qdrant`, `api`, `app` with healthcheck, volumes, `env_file` | CANCELLED | | 2026-10-07 14:59 | Docker dropped (plan v1.5): project runs locally |
| 0.8 | Verify `docker compose up --build` starts all three services | CANCELLED | | 2026-10-07 14:59 | Docker dropped (plan v1.5): project runs locally |
| 0.9 | Create `.env.example` with all keys and settings | COMPLETED | 2026-10-09 01:00 | 2026-10-09 01:00 | Created at repo root `.env.example` |
| 0.10 | Implement `config.py` (pydantic-settings) | COMPLETED | 2026-10-09 01:00 | 2026-10-09 01:00 | `api/config.py`: `Settings`, `model_for(role)`, `get_qdrant_client()`; relative paths resolve against repo root |
| 0.11 | LLM concurrency semaphore + 429 backoff helper; per-role model config | COMPLETED | 2026-10-09 01:00 | 2026-10-09 01:00 | `api/llm.py`: `call_llm()` semaphore + 429 backoff; per-role models via `Settings.model_for` |
| 0.12 | Define Pydantic schemas for all agent boundaries | COMPLETED | 2026-10-09 01:00 | 2026-10-09 01:00 | `api/schemas/models.py` |
| 0.13 | Define SQLite models (Run, TaskGraphLog, Report, WatchlistItem, Alert, SessionSummary) with WAL mode | COMPLETED | 2026-10-09 01:00 | 2026-10-09 01:00 | `api/db/` (SQLAlchemy); WAL and busy timeout set on connect; tables created via `init_db()` |
| 0.14 | Update `.gitignore` for `data/`, SQLite, local Qdrant storage | COMPLETED | 2026-10-09 01:00 | 2026-10-09 01:00 | Added SQLite WAL/SHM sidecar files; `data/`, `*.db` already covered |
| 0.15 | Update `Readme.md` with project structure and local (no Docker) setup | COMPLETED | 2026-10-09 01:00 | 2026-10-09 01:00 | Readme updated |
| 0.16 | GitHub Actions CI: API tests (uv + pytest) and app lint/build on push and PR to `main` | COMPLETED | 2026-10-07 00:03 | 2026-10-07 00:05 | First run passed: https://github.com/yasmin-poc-works/market_intelligence_engine/actions/runs/37512334244 |
| 0.17 | Extend CI with Docker build check (`docker compose config` and `build`) after 0.7 | CANCELLED | | 2026-10-07 14:59 | Docker dropped (plan v1.5): project runs locally |

## Phase 1: Context engineering layer

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 1.1 | Scratch store (`write_source`, `read_source`) | COMPLETED | 2026-10-09 01:45 | 2026-10-09 02:05 | `api/context/scratch_store.py`: JSON files under `data/scratch/`, id validated against path traversal |
| 1.2 | Token counter and `fit_to_budget` with compress-not-truncate | COMPLETED | 2026-10-09 01:45 | 2026-10-09 02:05 | `api/context/token_budget.py`: tiktoken count (offline fallback); compress lowest priority first, drop whole items only as last resort, never truncate |
| 1.3 | Run log recording cuts, reasons, and token counts per boundary | COMPLETED | 2026-10-09 01:45 | 2026-10-09 02:05 | `api/context/run_log.py`: per-boundary tokens and cuts with reasons; saved to `Run.run_log` |
| 1.4 | Memory store loading last 3 compressed session summaries | COMPLETED | 2026-10-09 01:45 | 2026-10-09 02:05 | `api/context/memory_store.py`: last 3 summaries oldest first; over-long ones compressed on save |
| 1.5 | Enable auto-summarization for long sessions | COMPLETED | 2026-10-09 01:45 | 2026-10-09 02:05 | `create_deep_agent` enables deepagents SummarizationMiddleware by default; `api/context/summarizer.py` provides the Groq summarizer for compression |

## Phase 2: Researcher agents

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 2.1 | Web Search Agent: SerpAPI integration | TODO | | | |
| 2.2 | Web Search Agent: full-body fetch and parse (top 5) | TODO | | | |
| 2.3 | Web Search Agent: credibility scoring (domain, recency decay, byline) | TODO | | | |
| 2.4 | Web Search Agent: paywall/login-wall detection and logging | TODO | | | |
| 2.5 | Document Reader Agent: Docling extraction and chunking | TODO | | | |
| 2.6 | Document Reader Agent: Qdrant `doc_chunks` with metadata, top-k=8 retrieval | TODO | | | |
| 2.7 | Entity Extractor Agent: entity and relationship extraction | TODO | | | |
| 2.8 | Entity Extractor Agent: Qdrant `entities` collection | TODO | | | |
| 2.9 | Entity Extractor Agent: `conflict: true` flagging | TODO | | | |

## Phase 3: Planner and orchestration

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 3.1 | Planner with `create_deep_agent()` and `write_todos` | TODO | | | |
| 3.2 | Typed `TaskGraph` output (3–6 sub-tasks, configurable cap of 6) | TODO | | | |
| 3.3 | Vague-brief detection with single clarifying question | TODO | | | |
| 3.4 | Spawn isolated researcher subagents via `task` tool (references only) | TODO | | | |
| 3.5 | LangGraph state machine with typed state and parallel join | TODO | | | |
| 3.6 | Persist task graph and status events to DB | TODO | | | |

## Phase 4: Synthesis, Writer, Fact-Checker

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 4.1 | Synthesis Agent: resolve refs, dedupe, confidence levels, ranking | TODO | | | |
| 4.2 | Synthesis Agent: enforce evidence token budget (default 6,000) | TODO | | | |
| 4.3 | Writer Agent: report structure with citations and confidence labels | TODO | | | |
| 4.4 | Writer Agent: grounding check with retry on unsupported claims | TODO | | | |
| 4.5 | Fact-Checker Agent: 20% sampling (min 5), similarity ≥ 0.85 check | TODO | | | |
| 4.6 | Fact-Checker Agent: `[UNVERIFIED]` marking and summary output | TODO | | | |

## Phase 5: FastAPI backend

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 5.1 | `POST /research/run` and PDF upload (≤10 files, ≤20 MB) | TODO | | | |
| 5.2 | `GET /research/status` (SSE) | TODO | | | |
| 5.3 | `/watchlist` CRUD | TODO | | | |
| 5.4 | `/reports` list and retrieve | TODO | | | |
| 5.5 | Export: `.md` and PDF | TODO | | | |
| 5.6 | JWT share link (7-day expiry) | TODO | | | |
| 5.7 | Error handling and CORS | TODO | | | |

## Phase 6: Scheduler and Diff Agent

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 6.1 | APScheduler watchlist runner (daily/weekly), single API worker | TODO | | | |
| 6.2 | Diff Agent (added/removed/changed, ignore rephrasing) | TODO | | | |
| 6.3 | In-app alerts | TODO | | | |
| 6.4 | Slack-compatible webhook delivery | TODO | | | |

## Phase 7: Next.js frontend

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 7.1 | Brief input panel (char counter, PDF drag-and-drop, toggles, runtime estimate) | TODO | | | |
| 7.2 | Live progress view (SSE rows, activity log, progress bar, elapsed time) | TODO | | | |
| 7.3 | Report viewer (sticky TOC, citation drawer, `[UNVERIFIED]` highlight, badges) | TODO | | | |
| 7.4 | Fact-check summary panel | TODO | | | |
| 7.5 | Watchlist dashboard (table, diff view, alert feed) | TODO | | | |
| 7.6 | Export buttons (PDF, `.md`, share link) | TODO | | | |

## Phase 8: Evaluation and deliverables

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 8.1 | Write 5 sample briefs with expected report structure in `samples/` | TODO | | | |
| 8.2 | Evaluation script and report (pass rate, citation accuracy, synthesis quality) | TODO | | | |
| 8.3 | README: local setup (uv, npm, Qdrant), API keys, env vars, architecture diagram | TODO | | | |
| 8.4 | README: context-engineering writeup with real blowout example from run logs | TODO | | | |
| 8.5 | README: sample report and end-to-end walkthrough | TODO | | | |
| 8.6 | Record 5–7 minute demo | TODO | | | |
| 8.7 | Publish to public repo `insightforge-agent` and post on LinkedIn | TODO | | | |

## Phase 9: Bonus (optional)

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 9.1 | LangSmith tracing | TODO | | | |
| 9.2 | Adaptive context budgets | TODO | | | |
| 9.3 | Citation trace | TODO | | | |
| 9.4 | Contradiction resolution UI | TODO | | | |
| 9.5 | Entity graph visualizer | TODO | | | |
| 9.6 | Source credibility tuning | TODO | | | |
| 9.7 | Multi-language research | TODO | | | |
| 9.8 | Optional: `docker-compose.prod.yml` override (built images, no bind mounts) | CANCELLED | | 2026-10-07 14:59 | Docker dropped (plan v1.5): project runs locally |
