"""Fact-Checker: samples cited claims, re-fetches their sources and checks that some passage
of the source is semantically close (cosine >= 0.85, via Qdrant) to the claim. Failures are
marked [UNVERIFIED] in the final report."""
import math
import random
import re
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Protocol

from qdrant_client import QdrantClient, models

from agents.writer import ReportDraft, render_report
from config import get_settings
from schemas import EvidenceBundle, EvidenceItem, FactCheckSummary

SAMPLE_RATE = 0.2
MIN_SAMPLES = 5
SIMILARITY_THRESHOLD = 0.85
_MARKER = re.compile(r"\[(E\d+)\]")

Refetch = Callable[[str], Awaitable[str]]  # url -> fresh page text


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Local sentence-transformers model (EMBEDDING_MODEL). Needs the sentence-transformers
    package, which is installed with the Phase 2 document tooling."""

    def __init__(self, model_name: str | None = None):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name or get_settings().embedding_model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()


@lru_cache
def default_embedder() -> Embedder:
    return SentenceTransformerEmbedder()


async def fetch_page_text(url: str) -> str:
    """Default re-fetch: GET the page and strip markup."""
    import httpx

    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(url, headers={"User-Agent": "InsightForge-FactChecker/0.1"})
        resp.raise_for_status()
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", resp.text)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()


def sample_size(n: int) -> int:
    return min(n, max(MIN_SAMPLES, math.ceil(SAMPLE_RATE * n)))


def cited_ids(draft: ReportDraft) -> list[str]:
    text = " ".join([draft.executive_summary] + [s.body for s in draft.sections])
    return list(dict.fromkeys(_MARKER.findall(text)))


def chunk_passages(text: str) -> list[str]:
    """Single sentences plus two-sentence windows."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]
    windows = [f"{a} {b}" for a, b in zip(sentences, sentences[1:])]
    return sentences + windows


def best_similarity(claim: str, source_texts: list[str], embedder: Embedder) -> float:
    """Highest cosine between the claim and any passage of the given source texts."""
    passages = [p for t in source_texts for p in chunk_passages(t)]
    if not passages:
        return 0.0
    vectors = embedder.embed(passages + [claim])
    client = QdrantClient(":memory:")
    try:
        client.create_collection(
            "factcheck", vectors_config=models.VectorParams(size=len(vectors[0]), distance=models.Distance.COSINE)
        )
        client.upsert(
            "factcheck",
            points=[models.PointStruct(id=i, vector=v) for i, v in enumerate(vectors[:-1])],
        )
        hits = client.query_points("factcheck", query=vectors[-1], limit=1).points
        return float(hits[0].score) if hits else 0.0
    finally:
        client.close()


async def fact_check(
    draft: ReportDraft,
    evidence: EvidenceBundle,
    sources: dict[str, dict],
    run_id: str,
    *,
    refetch: Refetch = fetch_page_text,
    embedder: Embedder,
    threshold: float = SIMILARITY_THRESHOLD,
) -> tuple[FactCheckSummary, set[str]]:
    """Returns the summary and the evidence ids that failed verification."""
    by_id: dict[str, EvidenceItem] = {e.id: e for e in evidence.items}
    population = [i for i in cited_ids(draft) if i in by_id]
    picked = random.Random(run_id).sample(population, sample_size(len(population)))

    failed: set[str] = set()
    for eid in picked:
        item = by_id[eid]
        texts = []
        for sid in item.source_ids:
            try:
                texts.append(await refetch(sources[sid]["url"]))
            except Exception:  # unreachable source counts as unverified
                continue
        if best_similarity(item.claim, texts, embedder) < threshold:
            failed.add(eid)

    total = len(picked)
    summary = FactCheckSummary(
        total_checked=total,
        passed=total - len(failed),
        failed=len(failed),
        pass_rate=(total - len(failed)) / total if total else 1.0,
        unverified_claims=[by_id[i].claim for i in picked if i in failed],
    )
    return summary, failed


def make_fact_check_stage(embedder: Embedder | None = None, refetch: Refetch = fetch_page_text):
    """Pipeline stage: state -> {"fact_check", "report"} with [UNVERIFIED] marks applied."""

    async def stage(state: dict) -> dict:
        emb = embedder or default_embedder()
        summary, failed = await fact_check(
            state["draft"], state["evidence"], state["sources"], state["run_id"], refetch=refetch, embedder=emb
        )
        old = state["report"]
        report = render_report(
            state["draft"],
            state["evidence"],
            state["sources"],
            state["run_id"],
            unverified=failed,
            fact_check=summary,
            report_id=old.id,
            created_at=old.created_at,
        )
        return {"fact_check": summary, "report": report}

    return stage
