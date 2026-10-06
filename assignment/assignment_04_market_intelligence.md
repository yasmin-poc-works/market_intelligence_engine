# Assignment — Autonomous Market Intelligence Engine
### InsightForge AI | Multi-Agent Research & Competitive Analysis Platform

---

## 🏢 Business Context

**Company:** InsightForge AI
**Industry:** Business Intelligence & Strategy
**Headquarters:** New York, NY

InsightForge serves competitive intelligence teams at mid-to-large enterprises across finance, SaaS, and consulting. Strategy teams at these companies spend 15–20 hours per week manually tracking competitors, reading industry reports, and assembling briefings. The output — a slide deck or PDF — is stale by the time it's circulated. Junior analysts are bottlenecked on data gathering instead of actual analysis.

**InsightForge AI** is an autonomous research platform where a user submits a natural-language research brief (e.g., *"Summarize the competitive landscape for B2B payroll software in Southeast Asia — focus on pricing, recent funding, and product gaps"*) and receives a structured, cited intelligence report within minutes. The platform also runs **scheduled monitoring jobs** that track a watchlist of companies or topics and alert users when something material changes.

**Your role:** You are a founding AI Engineer on the InsightForge platform team. You are responsible for building the multi-agent research pipeline, the scheduling system, and the analyst-facing report UI — end to end, production-ready.

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      NEXT.JS FRONTEND                        │
│   Brief input · Report viewer · Watchlist · Alert feed       │
└───────────────────────────┬──────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                     FASTAPI BACKEND                          │
│  /research/run      → one-shot brief execution               │
│  /research/status   → streaming SSE progress updates         │
│  /watchlist         → CRUD for monitored topics              │
│  /reports           → saved report retrieval + export        │
└──────────┬─────────────────┬───────────────────┬────────────┘
           │                 │                   │
           ▼                 ▼                   ▼
  ┌──────────────┐  ┌───────────────┐  ┌──────────────────┐
  │   PLANNER    │  │   SCHEDULER   │  │   MEMORY STORE   │
  │  create_deep │  │  APScheduler  │  │  Past reports    │
  │  _agent()    │  │  cron jobs    │  │  + entity graph  │
  │  root agent  │  │  per topic    │  │  (Qdrant)        │
  └──────┬───────┘  └───────────────┘  └──────────────────┘
         │ spawns via `task` tool (DeepAgents)
         ▼
┌──────────────────────────────────────────────────────────────┐
│                  RESEARCHER SUBAGENT POOL                    │
│      (parallel branches, isolated context per subagent)      │
│                                                              │
│  ┌─────────────┐   ┌─────────────┐   ┌──────────────────┐   │
│  │  Web        │   │  Doc        │   │  Entity          │   │
│  │  Search     │   │  Reader     │   │  Extractor       │   │
│  │  Agent      │   │  Agent      │   │  Agent           │   │
│  └──────┬──────┘   └──────┬──────┘   └────────┬─────────┘   │
└─────────┼────────────────┼──────────────────┼──────────────┘
          └────────────────┼──────────────────┘
                           ▼
               ┌──────────────────────┐
               │   SYNTHESIS AGENT    │
               │  Dedupes, resolves   │
               │  contradictions,     │
               │  ranks by recency    │
               └───────────┬──────────┘
                           ▼
               ┌──────────────────────┐
               │    WRITER AGENT      │
               │  Structured Markdown │
               │  report with inline  │
               │  citations           │
               └───────────┬──────────┘
                           ▼
               ┌──────────────────────┐
               │   FACT-CHECKER       │
               │  Verifies each claim │
               │  against source URLs │
               └──────────────────────┘
```

**State Machine (LangGraph):**

`PLANNING → RESEARCHING (parallel) → SYNTHESIZING → WRITING → FACT_CHECKING → COMPLETE`

The Planner fires first and produces a sub-task graph, then spawns the Researcher pool as subagents. LangGraph owns the overall state machine and waits for all parallel branches to report back before advancing to `SYNTHESIZING`. The Fact-Checker is the final gate before the report is marked `COMPLETE`.

---

## 🧩 Components

### 1. Planner Agent

Takes the user's free-text research brief and decomposes it into 3–6 parallel research sub-tasks. Each sub-task has explicit scope, a time horizon (e.g., "last 18 months"), and a preferred source type (web search, uploaded PDFs, or prior reports from memory).

**Requirements:**
- Instantiate the Planner as a root agent using `create_deep_agent()` from `deepagents`, with the `write_todos` tool tracking sub-task completion
- Spawn each Researcher (Web Search, Doc Reader, Entity Extractor) as an isolated subagent via the `task` tool — each subagent receives only its own sub-task query and scope, never the full brief or another subagent's raw output
- Output a typed task graph: `{ sub_tasks: [{ id, query, source_type, time_horizon, priority }] }`
- Detect when the brief is too vague and ask a single clarifying question before proceeding
- Log the task graph to the database so the UI can render the live progress view
- Respect a configurable maximum of 6 parallel sub-tasks to control cost and context sprawl

---

### 2. Context Engineering Layer

This is the load-bearing part of the assignment. A single research run can touch 30+ sources; none of that raw content should ever sit in one agent's prompt at once. You must design the system so every agent operates on the smallest slice of context it needs, and nothing more.

**Requirements:**

**Scratch store for raw content:**
- Every fetched web page, PDF chunk, and extracted entity is written to a keyed scratch store (DeepAgents' virtual filesystem, or an equivalent keyed store you implement) — never kept only in conversation history
- Downstream agents use a `read_source(source_id)`-style tool to pull specific content on demand, rather than having the full corpus preloaded into their prompt

**Token budget discipline:**
- The evidence bundle passed from Synthesis to Writer must respect a configurable token budget (default 6,000 tokens)
- When accumulated evidence exceeds budget, the Synthesis Agent must compress (summarize lower-priority items) rather than truncate arbitrarily — cite what you cut and why in the run log

**Subagent isolation:**
- Each Researcher subagent's prompt contains only: its assigned sub-task, relevant tool definitions, and nothing from sibling subagents or the original brief's full text
- The Planner's delegation prompt to each subagent must contain source **references** (URLs, chunk IDs) on completion — not raw fetched text — which the Synthesis Agent then resolves via the scratch store

**Memory compaction:**
- At startup, the Planner reads only the last 3 compressed session summaries from the Memory Store — never full historical transcripts
- Enable auto-summarization (`summarization=True` in the backend config, or equivalent) to compact older turns once a session's working context grows past budget

You will be asked in your README to justify, with a concrete example from your own run logs, one case where context engineering prevented a context-window blowout that a naive "pass everything through" implementation would have hit.

---

### 3. Web Search Agent

Retrieves up-to-date information from the web for each sub-task assigned to it.

**Requirements:**
- Integrates with Tavily, Brave Search, or SerpAPI (configurable via environment variable)
- Fetches and parses the **full article body** of the top 5 results per sub-task — not just snippets
- Scores each source for credibility using a simple rubric:
  - Domain type: `.edu`, `.gov`, major press > blogs > unknown
  - Publication date recency (decay function — older = lower score)
  - Author byline present: yes/no
- Writes `{ url, title, published_date, body_text, credibility_score }` per source to the shared scratch store, returning only the source ID and a one-line summary to the Planner
- Skips paywalled content gracefully (detect 402 / login-wall patterns) and logs skipped URLs

---

### 4. Document Reader Agent

Handles user-uploaded PDFs such as annual reports, analyst decks, and market research papers.

**Requirements:**
- Accepts up to 10 PDFs per research run
- Uses **Docling** for extraction and chunking into semantic sections
- Stores chunks in **Qdrant** with metadata: `{ source_filename, page_number, section_heading }`
- Given a sub-task query, retrieves the top-k most relevant chunks using semantic search (default k=8)
- Returns `{ chunks: [{ text, source, page, relevance_score }] }` — chunk IDs, not full text, are what get passed back up to the Planner; the Synthesis Agent resolves full text from Qdrant only when building the evidence bundle

---

### 5. Entity Extractor Agent

Identifies and structures named entities from all gathered content to enable conflict resolution downstream.

**Requirements:**
- Extract: companies, people, products, funding rounds, dollar figures, dates, and geographic regions
- Build a lightweight entity graph stored as JSON: `{ entities: [...], relationships: [{ subject, predicate, object, source_url, date }] }`
- Persist entity embeddings in **Qdrant** (a separate collection from document chunks) so the Planner can retrieve related past entities across research runs
- Flag conflicting facts about the same entity (e.g., two sources reporting different funding amounts for the same company) with a `conflict: true` marker
- Use spaCy or an LLM-based extraction prompt — either approach is acceptable

---

### 6. Synthesis Agent

Aggregates all outputs from the Researcher pool into a coherent, ranked evidence bundle.

**Requirements:**
- Resolves source IDs and chunk IDs from the scratch store / Qdrant — this is the one agent allowed to pull full raw content, since its job is compression
- De-duplicates facts using entity matching (same entity + same claim = deduplicate, keep highest-credibility source)
- Surfaces conflicts from the Entity Extractor with a `confidence` field: `HIGH | MEDIUM | LOW`
  - `HIGH`: 2+ independent sources agree
  - `MEDIUM`: single high-credibility source
  - `LOW`: single low-credibility source or conflicting signals
- Ranks evidence items by: recency first, then credibility score, then relevance to the original brief
- Outputs a structured, budget-respecting evidence bundle: `{ sections: [{ theme, evidence: [...], confidence }] }`

---

### 7. Writer Agent

Converts the evidence bundle into a polished, structured Markdown report.

**Requirements:**

The report must include the following sections in order:
- **Executive Summary** — ≤ 150 words, plain language, no jargon
- **Thematic sections** — headings driven by the sub-task graph produced by the Planner
- **Inline citations** — each factual claim ends with `[Source: {domain}, {date}, credibility: {score}]`
- **Confidence label per section** — `HIGH / MEDIUM / LOW` displayed below each section heading
- **Gaps & limitations** — a final section noting topics the research could not resolve and why

The Writer Agent must not invent facts. Every claim in the report must trace to a specific item in the evidence bundle. Implement a check: if the Writer references a fact not present in the bundle, the orchestrator must retry with an explicit constraint added to the prompt.

---

### 8. Fact-Checker Agent

Provides a final verification pass before the report is delivered to the user.

**Requirements:**
- Randomly samples 20% of extracted claims from the report (minimum 5 claims per report)
- For each sampled claim, re-fetches the original source URL and checks whether the claim is present in the page body using semantic similarity (cosine similarity ≥ 0.85 threshold, computed via Qdrant)
- Claims that fail the check are marked `[UNVERIFIED]` inline in the final report
- Outputs a fact-check summary: `{ total_checked, passed, failed, pass_rate, unverified_claims: [...] }`
- The fact-check summary is displayed in the UI alongside the report

---

### 9. Scheduler & Watchlist System

Enables continuous monitoring of topics and companies without requiring the user to manually re-run briefs.

**Requirements:**
- Users can add items to a watchlist: `{ topic, cadence: daily | weekly, last_run, next_run, webhook_url? }`
- **APScheduler** runs a research job per watchlist item on schedule, using the same pipeline as a manual brief
- A **Diff Agent** runs after each scheduled research job:
  - Compares the new report against the stored previous report for the same topic
  - Produces a change summary: `{ added_findings: [...], removed_findings: [...], changed_findings: [...] }`
  - Only surfaces changes that represent materially new information (not rephrasing of existing facts)
- Changed findings trigger an **in-app alert** visible in the alert feed
- If a `webhook_url` is configured, POST the change summary as JSON to that URL (supports Slack incoming webhooks)

---

### 10. Report UI (Next.js + FastAPI)

**Brief Input Panel:**
- Free-text brief input with a 500-character limit and a live character counter
- Optional PDF upload (drag-and-drop, up to 10 files, 20 MB each)
- Source preference toggles: Web Search on/off, Uploaded Docs on/off, Prior Reports on/off
- Estimated runtime displayed before submission (based on sub-task count)

**Live Progress View:**
- SSE-powered real-time status panel showing each sub-task as a row with status: `Queued / Running / Done / Failed`
- Per-agent activity log streaming below: e.g., "Web Search Agent: fetching reuters.com…"
- Overall progress bar and elapsed time

**Report Viewer:**
- Rendered Markdown with a sticky table of contents
- Inline citations are clickable — clicking opens a side drawer showing the source title, publication date, credibility score, and the exact paragraph the fact was drawn from
- `[UNVERIFIED]` claims are highlighted in amber with a tooltip explaining the flag
- Confidence labels displayed as colour-coded badges per section: green (HIGH), amber (MEDIUM), red (LOW)

**Watchlist Dashboard:**
- Table of monitored topics with last-run time, next-run time, and cadence
- One-click diff view: what changed since the last run
- Inline alert feed showing recent changes across all watched topics

**Export:**
- Download report as PDF (use a headless browser or `weasyprint`)
- Download report as `.md` file
- Copy a shareable read-only link (JWT-signed, expires in 7 days)

---

## 📦 Deliverables

1. **Multi-agent pipeline** — Planner as a DeepAgents root agent spawning the Researcher pool via the `task` tool, orchestrated end-to-end by a LangGraph state graph with typed state schemas between every agent transition
2. **Context engineering writeup** — a short section in your README documenting your scratch store design, token budget choices, and one concrete example of a context blowout your design prevented
3. **FastAPI backend** — `/research/run`, `/research/status` (SSE), `/watchlist` (CRUD), `/reports` (list + retrieve + export)
4. **Scheduler** — APScheduler-based watchlist runner with Diff Agent; webhook delivery for Slack
5. **Report UI** — Next.js frontend with brief input, live SSE progress, report viewer with citation drawer, watchlist dashboard, and export
6. **Sample brief library** — 5 pre-written research briefs covering different industries and question types, with expected report structure documented for each
7. **Evaluation report** — fact-check pass rate, citation accuracy, and synthesis quality assessed across the 5 sample briefs
8. **README** — setup guide, required API keys, environment variable reference, and a walkthrough of one end-to-end brief from input to exported PDF

---

## 📊 Evaluation Criteria

| Criteria | Weight | Description |
|---|---|---|
| **Multi-agent architecture** | 20% | Planner correctly spawns isolated subagents via the DeepAgents `task` tool; parallel branches complete before synthesis; every agent boundary uses typed state |
| **Context engineering** | 20% | Token budget respected on the evidence bundle; subagents never receive more than their own sub-task scope; raw content is retrieved on demand from the scratch store / Qdrant rather than preloaded |
| **Report quality** | 20% | Reports are coherent, well-structured, and faithfully reflect retrieved sources — assessed via manual review of the 5 sample briefs |
| **Fact-check pass rate** | 15% | ≥ 80% of spot-checked claims verified against source; `[UNVERIFIED]` applied correctly to failed claims |
| **Scheduler & diff** | 10% | Watchlist runs on schedule; Diff Agent surfaces genuine changes only and does not flag rephrasing as new information |
| **UI completeness** | 10% | Live SSE progress, citation drawer, watchlist dashboard, and PDF export all functional end-to-end |
| **Code quality** | 5% | Typed agent interfaces, modular agent files, no hardcoded API keys, clear error handling |

**Minimum passing threshold:** 70% overall weighted score, with **Multi-agent architecture** and **Context engineering** each ≥ 60% — these are the two skills this assignment exists to test, so a strong report built on a leaky, context-dumping pipeline will not pass.

---

## 🌟 Bonus Challenges

- **Contradiction resolution UI** — when the Synthesis Agent flags conflicting facts, surface them side-by-side in the UI and let the analyst choose which version to include before the report is written
- **Entity graph visualizer** — render the entity relationship graph as an interactive force-directed diagram; clicking a node shows all claims and sources associated with that entity
- **Source credibility tuning** — let users deprioritize or block specific domains before running a brief (e.g., exclude press releases, prefer peer-reviewed publications only)
- **Citation trace** — click any sentence in the final report and see the exact source paragraph it was derived from, highlighted in the citation drawer
- **Multi-language research** — accept briefs in Spanish, French, or German; retrieve and synthesize sources in those languages using a multilingual embedding model; output the report in the brief's original language
- **Adaptive context budgets** — dynamically raise or lower the evidence bundle token budget based on brief complexity (measured by sub-task count and source volume) instead of using a fixed default
- **LangSmith tracing** — instrument every agent call with LangSmith for a full per-run trace showing token counts, latency, and tool calls; include a screenshot in your README

---

## 🧰 Technology Stack

| Layer | Tool |
|---|---|
| Agent Framework | `deepagents` (subagent spawning via `task` tool) |
| Agent Orchestration | LangGraph (state machine across pipeline stages) |
| LLM | Claude claude-sonnet-4-6 (recommended) or GPT-4o |
| Web Search | Tavily API / Brave Search API / SerpAPI |
| PDF Extraction | Docling |
| Vector Store | Qdrant |
| Entity Extraction | spaCy or LLM-based prompt |
| Scheduler | APScheduler |
| Backend | FastAPI + SSE (via `sse-starlette`) |
| Frontend | Next.js 14+ (App Router) |
| PDF Export | WeasyPrint or Playwright headless |
| Observability | LangSmith (optional bonus) |

---

## 📌 Submission Instructions

1. Push to a public GitHub repository named `insightforge-agent`
2. `README.md` must include: setup instructions, a list of required API keys, an architecture diagram, your context engineering writeup (see Deliverables), and at least one sample report generated by the system
3. Record a **5–7 minute demo** showing: a brief being submitted, the live progress view updating in real time, the final report with a citation being clicked open, and a watchlist alert firing after a scheduled run
4. Post your demo on LinkedIn tagging the cohort.

---

*InsightForge AI is a fictional product built for learning purposes.*
