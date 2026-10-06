# Task Tracker: InsightForge AI Market Intelligence Engine

**Based on:** [plan.md](plan.md) v1.3
**Assignment:** [assignment_04_market_intelligence.md](../assignment/assignment_04_market_intelligence.md)

## How to use this file

- **Status values:** `TODO` → `IN PROGRESS` → `COMPLETED`
- When you start a task: set Status to `IN PROGRESS` and fill **Started** (`YYYY-MM-DD HH:MM`).
- When you finish: set Status to `COMPLETED` and fill **Completed**.
- Leave Started/Completed empty until they happen. Add a note in the Notes column for blockers or decisions.
- If the plan version changes, update the version above and add or adjust tasks.

## Progress summary

| Phase | Total | TODO | IN PROGRESS | COMPLETED |
|---|---|---|---|---|
| 0. Setup and foundations | 15 | 14 | 0 | 1 |
| 1. Context engineering layer | 5 | 5 | 0 | 0 |
| 2. Researcher agents | 9 | 9 | 0 | 0 |
| 3. Planner and orchestration | 6 | 6 | 0 | 0 |
| 4. Synthesis, Writer, Fact-Checker | 6 | 6 | 0 | 0 |
| 5. FastAPI backend | 7 | 7 | 0 | 0 |
| 6. Scheduler and Diff Agent | 4 | 4 | 0 | 0 |
| 7. Next.js frontend | 6 | 6 | 0 | 0 |
| 8. Evaluation and deliverables | 7 | 7 | 0 | 0 |
| 9. Bonus (optional) | 8 | 8 | 0 | 0 |

*Update these counts when statuses change.*

---

## Phase 0: Setup and foundations

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 0.1 | Groq smoke test: `deepagents` agent with one `task` subagent call (tool calling works) | TODO | | | |
| 0.2 | Scaffold `api/` (uv, `pyproject.toml` + `uv.lock`, FastAPI skeleton) | COMPLETED | 2026-10-06 17:51 | 2026-10-06 17:53 | Local venv uses Python 3.12; health test passes. Migrated pip to uv on 2026-10-06 23:29 (plan v1.3) |
| 0.3 | Scaffold `app/` (Next.js 14+, TypeScript, App Router) | TODO | | | |
| 0.4 | `api/Dockerfile` (python:3.11-slim, uv with `uv sync --frozen`, WeasyPrint system libs, CPU-only torch) | TODO | | | |
| 0.5 | `app/Dockerfile` (node:20-alpine, dev and multi-stage prod) | TODO | | | |
| 0.6 | `.dockerignore` files for `api/` and `app/` | TODO | | | |
| 0.7 | `docker-compose.yml`: `qdrant`, `api`, `app` with healthcheck, volumes, `env_file` | TODO | | | |
| 0.8 | Verify `docker compose up --build` starts all three services | TODO | | | |
| 0.9 | Create `.env.example` with all keys and settings | TODO | | | |
| 0.10 | Implement `config.py` (pydantic-settings) | TODO | | | |
| 0.11 | LLM concurrency semaphore + 429 backoff helper; per-role model config | TODO | | | |
| 0.12 | Define Pydantic schemas for all agent boundaries | TODO | | | |
| 0.13 | Define SQLite models (Run, TaskGraphLog, Report, WatchlistItem, Alert, SessionSummary) with WAL mode | TODO | | | |
| 0.14 | Update `.gitignore` for `data/`, SQLite, Qdrant volume storage | TODO | | | |
| 0.15 | Update `Readme.md` with project structure and Docker setup | TODO | | | |

## Phase 1: Context engineering layer

| ID | Task | Status | Started | Completed | Notes |
|---|---|---|---|---|---|
| 1.1 | Scratch store (`write_source`, `read_source`) | TODO | | | |
| 1.2 | Token counter and `fit_to_budget` with compress-not-truncate | TODO | | | |
| 1.3 | Run log recording cuts, reasons, and token counts per boundary | TODO | | | |
| 1.4 | Memory store loading last 3 compressed session summaries | TODO | | | |
| 1.5 | Enable auto-summarization for long sessions | TODO | | | |

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
| 8.3 | README: setup (Docker and non-Docker), API keys, env vars, architecture diagram | TODO | | | |
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
| 9.8 | Optional: `docker-compose.prod.yml` override (built images, no bind mounts) | TODO | | | |
