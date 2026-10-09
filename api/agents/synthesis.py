"""Synthesis: the only agent allowed to resolve references to raw content.

refs -> raw sources (scratch store / Qdrant chunks) -> atomic claims -> dedupe across sources
-> confidence -> ranking -> evidence token budget -> EvidenceBundle (+ source metadata
for citations). Raw bodies never leave this module.
"""
import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from config import get_settings
from context.run_log import RunLog
from context.scratch_store import ScratchStore, SourceNotFoundError
from context.token_budget import (
    BudgetItem,
    Summarizer,
    count_tokens,
    fit_to_budget,
    split_by_tokens,
)
from llm import call_llm, make_chat_model
from schemas import Confidence, EntityGraph, EvidenceBundle, EvidenceItem, SourceRef

HIGH_CREDIBILITY = 0.7  # single source at or above this is MEDIUM, below is LOW
DEDUPE_JACCARD = 0.6
EXTRACT_CHUNK_TOKENS = 3000

Extractor = Callable[[dict], Awaitable[list[str]]]  # {"title", "text"} -> atomic claims
ChunkLookup = Callable[[SourceRef], dict | None]  # resolves document_chunk/entity refs (Qdrant)

_STOP = set("a an the of in on at to for and or is are was were be been by with as from that this it its".split())


class ClaimList(BaseModel):
    claims: list[str]


def domain_of(url: str) -> str:
    host = re.sub(r"^[a-z]+://", "", url.lower()).split("/")[0].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", text)}


def _parse_date(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def resolve_ref(ref: SourceRef, store: ScratchStore, chunk_lookup: ChunkLookup | None = None) -> dict | None:
    """Raw record for a reference, or None if it cannot be resolved."""
    if ref.kind == "web":
        try:
            return store.read_source(ref.source_id)
        except (SourceNotFoundError, ValueError):
            return None
    return chunk_lookup(ref) if chunk_lookup else None


@dataclass
class _Cluster:
    claim: str
    source_ids: list[str]
    domains: set[str]
    credibility: float
    date: datetime | None


@dataclass
class SynthesisResult:
    bundle: EvidenceBundle
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)  # citation metadata only
    unresolved: list[str] = field(default_factory=list)


def _same_claim(a: str, b: str, entity_names: list[str]) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb and len(ta & tb) / len(ta | tb) >= DEDUPE_JACCARD:
        return True
    na, nb = _numbers(a), _numbers(b)
    if na and na == nb:  # same figures about the same entity
        la, lb = a.lower(), b.lower()
        return any(e in la and e in lb for e in entity_names)
    return False


def _relevance(claim: str, brief_tokens: set[str]) -> float:
    return len(_tokens(claim) & brief_tokens) / max(1, len(brief_tokens))


async def synthesize(
    refs: list[SourceRef],
    brief_text: str,
    *,
    store: ScratchStore,
    extractor: Extractor,
    summarizer: Summarizer,
    budget: int | None = None,
    entity_graph: EntityGraph | None = None,
    chunk_lookup: ChunkLookup | None = None,
    run_log: RunLog | None = None,
) -> SynthesisResult:
    budget = budget or get_settings().evidence_token_budget
    entities = entity_graph.entities if entity_graph else []
    entity_names = [e.name.lower() for e in entities]
    conflicted = [e.name.lower() for e in entities if e.conflict]

    # 1. resolve refs and extract claims (raw text stays inside this function)
    sources: dict[str, dict[str, Any]] = {}
    candidates: list[tuple[str, str]] = []  # (claim, source_id)
    unresolved: list[str] = []
    for ref in {r.source_id: r for r in refs}.values():  # dedupe refs by id
        rec = resolve_ref(ref, store, chunk_lookup)
        if rec is None:
            unresolved.append(ref.source_id)
            continue
        sources[ref.source_id] = {
            "url": rec.get("url", ""),
            "domain": domain_of(rec.get("url", "")),
            "title": rec.get("title", ""),
            "published_date": rec.get("published_date"),
            "credibility_score": float(rec.get("credibility_score", 0.0)),
        }
        for piece in split_by_tokens(rec.get("body_text", ""), EXTRACT_CHUNK_TOKENS):
            for claim in await extractor({"title": rec.get("title", ""), "text": piece}):
                if claim.strip():
                    candidates.append((claim.strip(), ref.source_id))

    # 2. dedupe by claim (highest-credibility phrasing wins; sources are merged)
    clusters: list[_Cluster] = []
    for claim, sid in sorted(candidates, key=lambda c: -sources[c[1]]["credibility_score"]):
        meta = sources[sid]
        for cl in clusters:
            if _same_claim(cl.claim, claim, entity_names):
                if sid not in cl.source_ids:
                    cl.source_ids.append(sid)
                cl.domains.add(meta["domain"])
                cl.credibility = max(cl.credibility, meta["credibility_score"])
                d = _parse_date(meta["published_date"])
                if d and (cl.date is None or d > cl.date):
                    cl.date = d
                break
        else:
            clusters.append(
                _Cluster(claim, [sid], {meta["domain"]}, meta["credibility_score"], _parse_date(meta["published_date"]))
            )

    # 3. confidence, 4. ranking (recency, then credibility, then relevance)
    brief_tokens = _tokens(brief_text)
    ranked = sorted(
        clusters,
        key=lambda c: (c.date.timestamp() if c.date else 0.0, c.credibility, _relevance(c.claim, brief_tokens)),
        reverse=True,
    )
    items: list[EvidenceItem] = []
    for i, cl in enumerate(ranked, 1):
        conflict = any(name in cl.claim.lower() for name in conflicted)
        if conflict:
            conf = Confidence.LOW
        elif len(cl.domains) >= 2:
            conf = Confidence.HIGH
        elif cl.credibility >= HIGH_CREDIBILITY:
            conf = Confidence.MEDIUM
        else:
            conf = Confidence.LOW
        items.append(
            EvidenceItem(
                id=f"E{i}",
                claim=cl.claim,
                source_ids=cl.source_ids,
                confidence=conf,
                published_date=cl.date,
                credibility_score=cl.credibility,
                conflict=conflict,
            )
        )

    # 5. evidence token budget: compress low-ranked claims, never truncate
    items, compressed = await asyncio.to_thread(_fit_evidence, items, budget, summarizer, run_log)
    used = {sid for it in items for sid in it.source_ids}
    bundle = EvidenceBundle(
        items=items,
        token_count=sum(count_tokens(_line_prefix(it)) + count_tokens(it.claim) for it in items),
        compressed=compressed,
    )
    return SynthesisResult(bundle, {k: v for k, v in sources.items() if k in used}, unresolved)


def _line_prefix(item: EvidenceItem) -> str:
    return f"[{item.id}] ({item.confidence.value}) "


def _fit_evidence(items: list[EvidenceItem], budget: int, summarizer: Summarizer, run_log: RunLog | None):
    overhead = sum(count_tokens(_line_prefix(it)) for it in items)
    n = len(items)
    budget_items = [BudgetItem(it.id, it.claim, priority=float(n - idx)) for idx, it in enumerate(items)]
    fitted = fit_to_budget(
        budget_items, max(0, budget - overhead), summarizer, boundary="synthesis->writer", run_log=run_log
    )
    by_id = {b.id: b.text for b in fitted}
    out = [it.model_copy(update={"claim": by_id[it.id]}) for it in items if it.id in by_id]
    changed = len(fitted) != len(items) or any(by_id[it.id] != it.claim for it in out)
    return out, changed


def make_llm_extractor() -> Extractor:
    model = make_chat_model("synthesis").with_structured_output(ClaimList)

    async def extract(chunk: dict) -> list[str]:
        prompt = (
            "Extract the atomic factual claims from this source text. One self-contained sentence per "
            "claim; keep company names, figures, dates and regions exactly as written; no opinions "
            f"or filler.\n\nTitle: {chunk['title']}\n\n{chunk['text']}"
        )
        out = await call_llm(lambda: model.ainvoke(prompt))
        return out.claims if isinstance(out, ClaimList) else ClaimList.model_validate(out).claims

    return extract


def make_synthesis_stage(
    extractor: Extractor,
    summarizer: Summarizer,
    *,
    store: ScratchStore | None = None,
    recorder=None,
    chunk_lookup: ChunkLookup | None = None,
):
    """Pipeline stage: state -> {"evidence", "sources"}."""

    async def stage(state: dict) -> dict[str, Any]:
        log = RunLog(state["run_id"])
        res = await synthesize(
            state.get("refs", []),
            state["brief"].text,
            store=store or ScratchStore(),
            extractor=extractor,
            summarizer=summarizer,
            entity_graph=state.get("entity_graph"),
            chunk_lookup=chunk_lookup,
            run_log=log,
        )
        if recorder is not None:
            recorder.save_run_log(state["run_id"], log)
        if not res.bundle.items:
            raise RuntimeError("synthesis produced no evidence from the collected sources")
        return {"evidence": res.bundle, "sources": res.sources}

    return stage
