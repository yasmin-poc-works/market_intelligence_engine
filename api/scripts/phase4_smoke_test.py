"""Manual check (not CI): real Groq claim extraction + report writing on synthetic sources.
The fact-check step needs the embedding model (Phase 2 install) and is covered by unit tests.

Run from api/:  uv run python scripts/phase4_smoke_test.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.synthesis import make_llm_extractor, synthesize
from agents.writer import make_llm_generator, render_report, write_report
from context.scratch_store import ScratchStore
from context.summarizer import make_llm_summarizer
from schemas import SourceRef, SubTask, TaskGraph

SOURCES = [
    ("Acme closes Series B", "https://techwire.com/acme", 0.85, "2026-05-02",
     "Acme, a battery startup, raised $50 million in a Series B round led by Orbit Capital. "
     "The company plans to hire 200 engineers in 2026. Its CEO said demand doubled."),
    ("Funding roundup", "https://dealnews.io/roundup", 0.6, "2026-05-03",
     "Orbit Capital led a $50 million Series B for Acme this week. Separately, the EV market grew 12% last year."),
]


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    store = ScratchStore(tempfile.mkdtemp())
    refs = [
        SourceRef(source_id=store.write_source(
            {"title": t, "url": u, "credibility_score": c, "published_date": d, "body_text": b}), summary=t)
        for t, u, c, d, b in SOURCES
    ]
    brief = "Acme funding and EV market outlook"
    res = await synthesize(refs, brief, store=store, extractor=make_llm_extractor(),
                           summarizer=make_llm_summarizer())
    for it in res.bundle.items:
        print(it.id, it.confidence.value, len(it.source_ids), it.claim)
    graph = TaskGraph(brief=brief, subtasks=[SubTask(id=f"t{i}", title=t, scope="s")
                                             for i, t in enumerate(["Funding", "Market", "Hiring"], 1)])
    draft = await write_report(brief, res.bundle, graph, {"t1": "done", "t2": "done", "t3": "done"},
                               make_llm_generator())
    print(render_report(draft, res.bundle, res.sources, "smoke").markdown)


asyncio.run(main())
