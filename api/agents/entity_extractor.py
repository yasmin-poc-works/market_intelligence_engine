"""Entity Extractor Agent: finds companies, people, products, funding, dollar figures, dates and
regions (plus the relationships between them) in the sources for a sub-task, stores them in the
Qdrant `entities` collection, and flags `conflict: true` when sources disagree on a fact.

The LLM only extracts; merging and conflict detection are plain code, so they are testable and
cannot be talked out of a disagreement."""
import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from agents.web_search import WebSearchAgent
from context.vector_store import VectorStore
from llm import call_llm, make_chat_model
from schemas import Entity, EntityGraph, Relationship, SourceRef, SubTask

log = logging.getLogger("insightforge")

EntityType = Literal["company", "person", "product", "funding", "figure", "date", "region"]
MAX_SOURCES = 3  # sources read per sub-task; bounds LLM tokens on free tiers
MAX_TEXT_CHARS = 3500
MAX_ENTITIES = 12  # long lists make small models emit broken JSON
NUMBER_TOLERANCE = 0.01  # figures within 1% are the same figure


class Attribute(BaseModel):
    key: str
    value: str


class ExtractedEntity(BaseModel):
    name: str
    type: EntityType
    attributes: list[Attribute] = Field(default_factory=list)


class ExtractedRelationship(BaseModel):
    source: str
    target: str
    relation: str


class ExtractedEntities(BaseModel):
    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)


EntityExtract = Callable[[dict], Awaitable[ExtractedEntities]]  # {"title", "text"} -> entities


def make_llm_entity_extractor() -> EntityExtract:
    # JSON-schema mode is steadier than tool calling for long structured outputs on Groq's gpt-oss models
    model = make_chat_model("entity").with_structured_output(ExtractedEntities, method="json_schema")

    async def extract(source: dict) -> ExtractedEntities:
        prompt = (
            f"Extract at most {MAX_ENTITIES} of the most important entities from this text: companies, people, "
            "products, funding rounds, dollar figures, dates and regions. Put facts about an entity in its "
            "attributes (at most 3 each, for example funding_total, headquarters, revenue, founded). Add "
            "relationships between entities (for example 'invested in', 'acquired', 'competes with'). "
            "Copy names and figures exactly as written; do not infer. Use plain ASCII punctuation.\n\n"
            f"Title: {source['title']}\n\n{source['text']}"
        )
        out = await call_llm(lambda: model.ainvoke(prompt))
        return out if isinstance(out, ExtractedEntities) else ExtractedEntities.model_validate(out)

    return extract


# ---------- normalization and conflict detection ----------
_SUFFIXES = re.compile(r"\b(inc|incorporated|ltd|limited|llc|plc|gmbh|corp|corporation|co|ag|sa|bv|nv)\b\.?", re.I)
_MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9,
                "t": 1e12, "trillion": 1e12}
_NUMBER = re.compile(r"(\d[\d,]*\.?\d*)\s*(thousand|million|billion|trillion|bn|mn|k|m|b|t)?\b", re.I)


def normalize_name(name: str) -> str:
    cleaned = _SUFFIXES.sub(" ", name.lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", cleaned)).strip()


def entity_key(name: str, type_: str) -> str:
    return f"{type_}:{normalize_name(name)}"


def _numbers(value: str) -> list[float]:
    out = []
    for raw, unit in _NUMBER.findall(value):
        try:
            out.append(float(raw.replace(",", "")) * _MULTIPLIERS.get(unit.lower(), 1.0))
        except ValueError:
            continue
    return out


def same_value(a: str, b: str) -> bool:
    na, nb = _numbers(a), _numbers(b)
    if na and nb:
        if len(na) != len(nb):
            return False
        return all(abs(x - y) <= NUMBER_TOLERANCE * max(abs(x), abs(y), 1e-9) for x, y in zip(sorted(na), sorted(nb), strict=True))
    norm = lambda v: re.sub(r"[^a-z0-9]+", " ", v.lower()).strip()  # noqa: E731
    return norm(a) == norm(b)


def build_graph(entity_records: list[dict[str, Any]], relationship_records: list[dict[str, Any]]) -> EntityGraph:
    """Merge stored observations into an entity graph. For each attribute the value from the most
    credible source wins; a differing value from another source sets `conflict` and is kept as
    '<attribute> (alternate)'."""
    entities: list[Entity] = []
    known: set[str] = set()
    for rec in sorted(entity_records, key=lambda r: r["key"]):
        observations = sorted(rec["observations"], key=lambda o: -float(o.get("credibility", 0)))
        attributes: dict[str, str] = {}
        conflict = False
        for obs in observations:
            for key, value in obs.get("attributes", {}).items():
                slot = key.strip().lower()
                if slot not in attributes:
                    attributes[slot] = value
                elif not same_value(attributes[slot], value):
                    conflict = True
                    attributes.setdefault(f"{slot} (alternate)", value)
        entities.append(
            Entity(
                id=rec["key"],
                name=rec["name"],
                type=rec["type"],
                attributes=attributes,
                source_ids=list(dict.fromkeys(o["source_id"] for o in observations)),
                conflict=conflict,
            )
        )
        known.add(rec["key"])

    relationships: dict[tuple[str, str, str], Relationship] = {}
    for rel in relationship_records:
        if rel["source_key"] not in known or rel["target_key"] not in known:
            continue
        k = (rel["source_key"], rel["relation"].strip().lower(), rel["target_key"])
        existing = relationships.get(k)
        if existing is None:
            relationships[k] = Relationship(
                source=rel["source_key"], target=rel["target_key"], relation=rel["relation"].strip(),
                source_ids=[rel["source_id"]],
            )
        elif rel["source_id"] not in existing.source_ids:
            existing.source_ids.append(rel["source_id"])
    return EntityGraph(entities=entities, relationships=list(relationships.values()))


def load_graph(store: VectorStore, run_id: str) -> EntityGraph:
    entities, relationships = store.load_entity_records(run_id)
    return build_graph(entities, relationships)


def observations_from(extracted: ExtractedEntities, source_id: str, credibility: float):
    """(entity observations, relationship records) for one source."""
    observations = []
    keys: dict[str, str] = {}
    for e in extracted.entities:
        if not normalize_name(e.name):
            continue
        key = entity_key(e.name, e.type)
        keys[normalize_name(e.name)] = key
        observations.append(
            {
                "key": key,
                "name": e.name.strip(),
                "type": e.type,
                "source_id": source_id,
                "credibility": credibility,
                "attributes": {a.key: a.value for a in e.attributes if a.key.strip() and a.value.strip()},
            }
        )
    relationships = []
    for r in extracted.relationships:
        src, dst = keys.get(normalize_name(r.source)), keys.get(normalize_name(r.target))
        if src and dst and r.relation.strip():
            relationships.append({"source_key": src, "target_key": dst, "relation": r.relation.strip(), "source_id": source_id})
    return observations, relationships


class EntityExtractorAgent:
    def __init__(self, search: WebSearchAgent, store: VectorStore, extractor: EntityExtract, max_sources: int = MAX_SOURCES):
        self._search = search
        self._store = store
        self._extract = extractor
        self._max_sources = max_sources

    async def run(self, run_id: str, subtask: SubTask) -> list[SourceRef]:
        refs = await self._search.run(subtask)  # also the evidence the entities came from
        all_obs: list[dict[str, Any]] = []
        all_rels: list[dict[str, Any]] = []
        for ref in refs[: self._max_sources]:
            record = self._search.store.read_source(ref.source_id)
            try:
                extracted = await self._extract(
                    {"title": record.get("title", ""), "text": record.get("body_text", "")[:MAX_TEXT_CHARS]}
                )
            except Exception as exc:  # entities are a bonus layer: the sources still count as evidence
                log.warning("entity extraction failed for %s: %s: %s", ref.url, type(exc).__name__, str(exc)[:200])
                continue
            obs, rels = observations_from(extracted, ref.source_id, float(record.get("credibility_score", 0.5)))
            all_obs += obs
            all_rels += rels
        await asyncio.to_thread(self._store.record_entity_observations, run_id, all_obs)
        await asyncio.to_thread(self._store.record_relationships, run_id, all_rels)
        return refs
