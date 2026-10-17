# InsightForge AI: project summary

InsightForge AI is a multi-agent market intelligence engine that I built. You give it a research brief, for example *"Summarize the competitive landscape for B2B payroll software in Southeast Asia"*, and optionally add PDFs, PDF links or web pages. It plans the research, searches the web and reads your documents in parallel, and writes a structured report where **every statement carries a citation** with its source, date and credibility score. A fact-checker then re-fetches a sample of the cited sources and flags claims they do not support. A watchlist re-runs topics on a schedule and alerts me when something material changes.

**Demo video:** [watch on YouTube](https://youtu.be/eSY4FOThyW4). For setup and the full technical write-up, see the [README](Readme.md). A real generated report is in [docs/sample_report.md](docs/sample_report.md).

## Why I built it

Most LLM research tools write fluent text that is not tied to evidence, and long research runs overflow the model's context window. I wanted to see how far careful engineering can get on both problems:

1. **Grounding.** The writer may only use a numbered evidence list. Every sentence must cite it, and code (not the model) rejects sentences whose citation or figures do not match the evidence.
2. **Context engineering.** Agents never pass raw text to each other. Researchers store what they find and return only an id with a one-line summary. A single stage reads the raw text, under a token budget.

## How it works

```
Brief (+ PDFs / links)
  → Planner: splits the brief into 3 to 6 self-contained sub-tasks
  → in parallel: Web researcher · Document reader · Entity extractor
  → join
  → Synthesis: claims, de-duplication, confidence, token budget
  → Writer: cited report, every sentence checked against the evidence
  → Fact-checker: re-fetches about 20% of the cited sources (cosine similarity ≥ 0.85)
  → Report, with live progress streamed to the UI throughout
```

- **Frontend:** Next.js, with a live progress view, a report viewer where clicking a citation opens the source, exports to Markdown and PDF, share links, and a watchlist.
- **Backend:** FastAPI and a LangGraph pipeline with typed state at every step. Progress is streamed over server-sent events.
- **Storage:** SQLite for runs, events, reports and alerts; embedded Qdrant for PDF passages and entities; a file-based scratch store for web pages.
- **Scheduling:** APScheduler re-runs watchlist topics daily or weekly. A Diff agent compares each new report with the last one and raises an in-app alert and a Slack-compatible webhook.
- **Stack:** Groq (`gpt-oss-120b`), SerpAPI, DeepAgents (planner), LangGraph, Qdrant, SQLite, Docling, sentence-transformers, FastAPI, APScheduler, Next.js, uv.

## Key design decisions

- **References, not content, between agents.** A researcher returns an id and at most 300 characters. Raw text stays in the scratch store or Qdrant. This keeps each agent's context small and makes every run easy to debug.
- **Isolation.** Each researcher sees only its own sub-task. That forced the planner to write self-contained sub-tasks. A real run showed what happens otherwise (a search query that lost the topic), so the planner now writes an explicit short search query.
- **Token budget: compress, then drop, never truncate.** Every cut is logged. In one real run, 13 articles became at least 242 claims (about 6,071 tokens) against a 4,000-token budget: 185 cuts, 142 dropped and 43 compressed. The log also showed that compressing short claims was wasteful (43 extra model calls saved only 575 tokens), so I stopped compressing them. I did not re-measure after that change.
- **Grounding enforced in code.** Code checks that each sentence cites known evidence and that its figures appear in it, retries with the specific violations, and removes what still fails. Evidence ids are turned into `[Source: domain, date, credibility: x.xx]` only after that.
- **One failure does not stop the run.** A failed researcher is recorded and the run continues. The run fails only if every researcher fails.
- **Built for rate limits.** The free Groq tier allows 8,000 tokens per minute, so there is a concurrency limit and a retry that honours the provider's retry hints.
- **Session memory.** The planner sees compressed summaries of the last three runs.

## Features I added after the first version

- **Reference web pages and PDF links** as sources I supply myself. Paywalled pages are skipped and reported in the live log.
- **Documents survive the clarifying question.** For a vague brief the planner asks one question, and the continued run reuses the files and links from the first submission.
- **Source reuse and cleanup.** A brief related to the last run (embedding similarity ≥ 0.45) reuses its saved pages and PDFs. An unrelated brief clears older stored data, and anything older than 24 hours is swept. The database history is never deleted. I set the threshold from real embeddings: unrelated briefs scored 0.14 to 0.27, and briefs about the same subject scored 0.57 to 0.89.
- **Better failure visibility.** Failed stages now log a traceback and name the stage in the stored error, and the writer tolerates a model reply that returns the schema instead of data.

## Results

I tested honestly, and the results are below my own targets.

| | Result | Target |
|---|---|---|
| Fact-check pass rate | 62% | 80% |
| Citation accuracy | 59% | 80% |
| Synthesis score (automated proxies) | 0.68 | n/a |

- Only **1 of the 5** sample briefs was run on real services (`payroll-sea`, after four live runs to get one clean result). Each run takes about 15 minutes and uses a large share of a free quota.
- The median similarity of a claim to its own source is 0.91, but the 0.85 threshold is strict for paraphrased claims: 65% of claims pass at 0.85 and 83% at 0.75. About one source in four could not be re-fetched, and those count as unverified. I deliberately left the threshold at 0.85 rather than lowering it to hit the target.
- Fixes made after the last full evaluation run were not re-measured.
- 233 automated tests pass, using fakes instead of the network or a model.

## What I learned

- Live runs found bugs that no unit test did: search queries that dropped the topic, API timeouts, a non-retryable 429 error, tool-call failures on structured output, duplicate citations, and a fact-checker blocked because it used a different browser identity from the researcher.
- Models do not always follow a schema, so parsing and retries have to be defensive at every model boundary.
- Saving an error to the database is not the same as being able to see it. A failed run was invisible in the server log until I added logging.

## Known limitations

- On the free model tier, one run takes about 15 minutes.
- Confidence labels are conservative: most sections come out LOW because typical sites score 0.5 to 0.7 for credibility.
- Deciding "same topic or a new one" is a similarity guess. A long follow-up that names no subject can be misjudged and clear old data.
- Downloaded reports show citations as plain text; the web app opens them in a source drawer.
- PDF export uses Latin-1 fonts, so non-Latin text becomes `?`.
- LangSmith tracing is not wired up.

## What I would do next

Run the other four evaluation briefs on a paid tier, re-measure the fixes, add LangSmith tracing, tune domain credibility, make exported citations clickable, and add a "start fresh" option for the topic detection.
