"""Manual check (not CI) of the real researchers: live SerpAPI search + article fetch, Docling on a
generated PDF, and one Groq entity extraction. Uses one search and one small LLM call.

Run from api/:  uv run python scripts/phase2_smoke_test.py [web|docs|entities|all]
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fpdf import FPDF
from qdrant_client import QdrantClient

from agents.doc_reader import DocumentReaderAgent, make_chunk_lookup
from agents.entity_extractor import EntityExtractorAgent, load_graph, make_llm_entity_extractor
from agents.web_search import WebSearchAgent
from context.scratch_store import ScratchStore
from context.vector_store import VectorStore
from schemas import SubTask
from tools.search import get_search_provider

TASK = SubTask(
    id="t1",
    title="European EV charging funding",
    scope="Recent funding rounds of European EV charging companies in 2025 and 2026",
)


async def web() -> WebSearchAgent:
    agent = WebSearchAgent(get_search_provider(), ScratchStore(tempfile.mkdtemp()))
    outcome = await agent.search_and_store(TASK)
    print(f"\n== web: {len(outcome.refs)} sources, {len(outcome.skipped)} skipped")
    for ref in outcome.refs:
        rec = agent.store.read_source(ref.source_id)
        print(f"  [{rec['credibility_score']:.2f}] {rec['url']} ({len(rec['body_text'])} chars, byline={rec['byline']})")
    for url, reason in outcome.skipped:
        print(f"  skipped {url}: {reason}")
    return agent


async def docs() -> None:
    folder = Path(tempfile.mkdtemp()) / "run1"
    folder.mkdir()
    pdf = FPDF()
    for heading, body in [
        ("Revenue", "Revenue grew 12% to 4.2 billion dollars in 2025. " * 4),
        ("Credit losses", "Credit losses were 1.1% of volume, down from 1.4%. " * 4),
    ]:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, body)
    pdf.output(str(folder / "1_report.pdf"))

    store = VectorStore(QdrantClient(":memory:"))
    agent = DocumentReaderAgent(store, folder.parent)
    refs = await agent.run("run1", SubTask(id="t", title="Credit losses", scope="loss rates", researcher="document"))
    print(f"\n== docs: {len(refs)} chunk refs")
    lookup = make_chunk_lookup(store)
    for ref in refs[:3]:
        rec = lookup(ref)
        print(f"  {rec['title']}: {rec['body_text'][:70]!r}")


async def entities(agent: WebSearchAgent) -> None:
    store = VectorStore(QdrantClient(":memory:"))
    ent = EntityExtractorAgent(agent, store, make_llm_entity_extractor(), max_sources=1)
    await ent.run("run1", TASK)
    graph = load_graph(store, "run1")
    print(f"\n== entities: {len(graph.entities)} entities, {len(graph.relationships)} relationships")
    for e in graph.entities[:8]:
        print(f"  {e.type}: {e.name} {dict(list(e.attributes.items())[:2])} conflict={e.conflict}")


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    agent = None
    if what in ("web", "all", "entities"):
        agent = await web()
    if what in ("docs", "all"):
        await docs()
    if what in ("entities", "all"):
        await entities(agent)


if __name__ == "__main__":
    asyncio.run(main())
