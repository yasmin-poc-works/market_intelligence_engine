"""Document Reader Agent: indexes a run's uploaded PDFs into Qdrant (`doc_chunks`, with source
filename, page number and section heading) and, for a sub-task, returns the top-8 most relevant
chunk ids. Only ids and one-line summaries leave this agent; the synthesis stage resolves the
ids back to text through `make_chunk_lookup`."""
import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from context.vector_store import TOP_K, VectorStore
from schemas import SourceRef, SubTask
from tools.documents import display_name, extract_chunks

log = logging.getLogger("insightforge")

DOCUMENT_CREDIBILITY = 0.75  # user-supplied material: trusted more than the open web, less than primary filings
Extractor = Callable[[Path], list[dict[str, Any]]]


def document_url(chunk_id: str, payload: dict[str, Any]) -> str:
    """Pseudo-URL used as the citation source: the domain part is the file name."""
    return f"document://{payload['source_filename']}/page-{payload['page_number']}/{chunk_id}"


def chunk_id_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def chunk_record(chunk_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Shape the synthesis stage expects for a source."""
    heading = f" - {payload['section_heading']}" if payload.get("section_heading") else ""
    return {
        "url": document_url(chunk_id, payload),
        "title": f"{payload['source_filename']}, p.{payload['page_number']}{heading}",
        "published_date": None,
        "body_text": payload["text"],
        "credibility_score": DOCUMENT_CREDIBILITY,
    }


def make_chunk_lookup(store: VectorStore):
    """ChunkLookup for synthesis: resolves document_chunk refs."""

    def lookup(ref: SourceRef) -> dict[str, Any] | None:
        if ref.kind != "document_chunk":
            return None
        payload = store.get_chunk(ref.source_id)
        return chunk_record(ref.source_id, payload) if payload else None

    return lookup


class DocumentReaderAgent:
    def __init__(self, store: VectorStore, upload_dir: Path, extractor: Extractor = extract_chunks, top_k: int = TOP_K):
        self._store = store
        self._upload_dir = upload_dir
        self._extract = extractor
        self._top_k = top_k
        self._locks: dict[str, asyncio.Lock] = {}

    async def ensure_indexed(self, run_id: str) -> int:
        """Extract and index the run's PDFs once (parallel document sub-tasks share the work)."""
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            folder = self._upload_dir / run_id
            pdfs = sorted(folder.glob("*.pdf")) if folder.is_dir() else []
            indexed = 0
            for pdf in pdfs:
                if await asyncio.to_thread(self._store.has_documents, run_id, display_name(pdf.name)):
                    continue
                chunks = await asyncio.to_thread(self._extract, pdf)
                ids = await asyncio.to_thread(self._store.add_chunks, run_id, chunks)
                log.info("indexed %s: %d chunks", pdf.name, len(ids))
                indexed += len(ids)
            return indexed

    async def run(self, run_id: str, subtask: SubTask) -> list[SourceRef]:
        await self.ensure_indexed(run_id)
        query = f"{subtask.title}. {subtask.scope}"
        hits = await asyncio.to_thread(self._store.search_chunks, run_id, query, self._top_k)
        if not hits:
            raise RuntimeError(f"no relevant passages found in the uploaded documents for '{subtask.title}'")
        refs = []
        for hit in hits:
            p = hit.payload
            snippet = p["text"][:180].replace("\n", " ")
            refs.append(
                SourceRef(
                    source_id=hit.id,
                    summary=f"{p['source_filename']} p.{p['page_number']}: {snippet}"[:300],
                    kind="document_chunk",
                    url=document_url(hit.id, p),
                )
            )
        return refs

