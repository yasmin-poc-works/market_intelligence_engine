"""Production wiring of the research pipeline (real LLM stages, search, Qdrant)."""
import asyncio

from agents.doc_reader import DocumentReaderAgent, chunk_id_from_url, make_chunk_lookup
from agents.entity_extractor import EntityExtractorAgent, load_graph, make_llm_entity_extractor
from agents.fact_checker import fetch_page_text, make_fact_check_stage
from agents.synthesis import make_llm_extractor, make_synthesis_stage
from agents.web_search import WebSearchAgent
from agents.writer import make_llm_generator, make_write_stage
from config import get_settings
from context.scratch_store import ScratchStore
from context.summarizer import make_llm_summarizer
from context.vector_store import VectorStore
from graph.events import EventRecorder
from graph.pipeline import PipelineDeps
from services.research_runtime import LazySearchProvider, make_researcher

DOCUMENT_SCHEME = "document://"


def build_deps(recorder: EventRecorder) -> PipelineDeps:
    settings = get_settings()
    scratch = ScratchStore()
    vectors = VectorStore()

    web = WebSearchAgent(LazySearchProvider(), scratch)
    docs = DocumentReaderAgent(vectors, settings.upload_dir)
    entities = EntityExtractorAgent(web, vectors, make_llm_entity_extractor())

    base_synthesis = make_synthesis_stage(
        make_llm_extractor(),
        make_llm_summarizer(),
        store=scratch,
        recorder=recorder,
        chunk_lookup=make_chunk_lookup(vectors),
    )

    async def synthesize(state: dict) -> dict:
        graph = await asyncio.to_thread(load_graph, vectors, state["run_id"])
        update = await base_synthesis({**state, "entity_graph": graph if graph.entities else None})
        return {**update, "entity_graph": graph}

    async def refetch(url: str) -> str:
        if url.startswith(DOCUMENT_SCHEME):  # uploaded document: check against the stored passage
            payload = vectors.get_chunk(chunk_id_from_url(url))
            if payload is None:
                raise LookupError(url)
            return payload["text"]
        return await fetch_page_text(url)

    async def load_references(urls: list[str]):
        outcome = await web.fetch_urls(urls)
        return outcome.refs, outcome.skipped

    return PipelineDeps(
        recorder=recorder,
        researcher=make_researcher(web, docs, entities),
        references=load_references,
        synthesize=synthesize,
        write=make_write_stage(make_llm_generator()),
        fact_check=make_fact_check_stage(refetch=refetch),
    )
