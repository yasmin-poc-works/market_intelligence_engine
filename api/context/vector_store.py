"""Qdrant collections for the researchers: `doc_chunks` (uploaded document passages) and
`entities` (entities and relationships). Everything is scoped by run_id, so parallel and
repeated runs never see each other's data. Works with embedded and remote Qdrant."""
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient, models

from agents.fact_checker import Embedder, default_embedder
from config import get_qdrant_client

DOC_COLLECTION = "doc_chunks"
ENTITY_COLLECTION = "entities"
TOP_K = 8


@dataclass
class ChunkHit:
    id: str
    score: float
    payload: dict[str, Any]


def _run_filter(run_id: str, **extra: str) -> models.Filter:
    conditions = [models.FieldCondition(key="run_id", match=models.MatchValue(value=run_id))]
    conditions += [models.FieldCondition(key=k, match=models.MatchValue(value=v)) for k, v in extra.items()]
    return models.Filter(must=conditions)


def point_id(*parts: str) -> str:
    """Deterministic id, so re-indexing the same data overwrites instead of duplicating."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))


class VectorStore:
    def __init__(self, client: QdrantClient | None = None, embedder: Embedder | None = None):
        self._client = client
        self._embedder = embedder
        self._lock = threading.RLock()  # embedded Qdrant is not built for concurrent writers
        self._ready: set[str] = set()

    @property
    def client(self) -> QdrantClient:
        if self._client is None:
            self._client = get_qdrant_client()
        return self._client

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = default_embedder()
        return self._embedder

    def _ensure(self, name: str, dim: int) -> None:
        if name in self._ready:
            return
        if not self.client.collection_exists(name):
            self.client.create_collection(
                name, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE)
            )
        self._ready.add(name)

    def delete_run(self, run_id: str) -> int:
        """Remove every point (document passages, entities, relationships) a run stored; returns the count."""
        removed = 0
        with self._lock:
            for name in (DOC_COLLECTION, ENTITY_COLLECTION):
                if not self.client.collection_exists(name):
                    continue
                flt = _run_filter(run_id)
                removed += self.client.count(name, count_filter=flt, exact=True).count
                self.client.delete(name, points_selector=models.FilterSelector(filter=flt))
        return removed

    # ----- document chunks -----
    def has_documents(self, run_id: str, filename: str) -> bool:
        with self._lock:
            if not self.client.collection_exists(DOC_COLLECTION):
                return False
            found, _ = self.client.scroll(
                DOC_COLLECTION, scroll_filter=_run_filter(run_id, source_filename=filename), limit=1
            )
            return bool(found)

    def add_chunks(self, run_id: str, chunks: list[dict[str, Any]]) -> list[str]:
        """chunks: {text, source_filename, page_number, section_heading}. Returns point ids."""
        if not chunks:
            return []
        vectors = self.embedder.embed([c["text"] for c in chunks])
        ids = [point_id(run_id, c["source_filename"], str(i), c["text"][:80]) for i, c in enumerate(chunks)]
        with self._lock:
            self._ensure(DOC_COLLECTION, len(vectors[0]))
            self.client.upsert(
                DOC_COLLECTION,
                points=[
                    models.PointStruct(id=pid, vector=vec, payload={**c, "run_id": run_id})
                    for pid, vec, c in zip(ids, vectors, chunks, strict=True)
                ],
            )
        return ids

    def search_chunks(self, run_id: str, query: str, k: int = TOP_K) -> list[ChunkHit]:
        vector = self.embedder.embed([query])[0]
        with self._lock:
            if not self.client.collection_exists(DOC_COLLECTION):
                return []
            res = self.client.query_points(
                DOC_COLLECTION, query=vector, query_filter=_run_filter(run_id), limit=k, with_payload=True
            )
        return [ChunkHit(str(p.id), float(p.score), dict(p.payload or {})) for p in res.points]

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        with self._lock:
            if not self.client.collection_exists(DOC_COLLECTION):
                return None
            try:
                found = self.client.retrieve(DOC_COLLECTION, ids=[chunk_id], with_payload=True)
            except Exception:  # malformed id
                return None
        return dict(found[0].payload or {}) if found else None

    # ----- entities -----
    def record_entity_observations(self, run_id: str, observations: list[dict[str, Any]]) -> None:
        """Append raw observations per entity key: {key, name, type, source_id, credibility,
        attributes}. The merged graph (and conflict flags) is derived from them on read."""
        if not observations:
            return
        with self._lock:
            by_key: dict[str, list[dict[str, Any]]] = {}
            for o in observations:
                by_key.setdefault(o["key"], []).append(o)
            vectors = self.embedder.embed([f"{v[0]['name']} ({v[0]['type']})" for v in by_key.values()])
            self._ensure(ENTITY_COLLECTION, len(vectors[0]))
            points = []
            for (key, obs), vec in zip(by_key.items(), vectors, strict=True):
                pid = point_id(run_id, "entity", key)
                existing = self.client.retrieve(ENTITY_COLLECTION, ids=[pid], with_payload=True)
                prior = list((existing[0].payload or {}).get("observations", [])) if existing else []
                points.append(
                    models.PointStruct(
                        id=pid,
                        vector=vec,
                        payload={
                            "run_id": run_id,
                            "kind": "entity",
                            "key": key,
                            "name": obs[0]["name"],
                            "type": obs[0]["type"],
                            "observations": prior + [{k: v for k, v in o.items() if k not in ("name", "type")} for o in obs],
                        },
                    )
                )
            self.client.upsert(ENTITY_COLLECTION, points=points)

    def record_relationships(self, run_id: str, relationships: list[dict[str, Any]]) -> None:
        """relationships: {source_key, target_key, relation, source_id}."""
        if not relationships:
            return
        texts = [f"{r['source_key']} {r['relation']} {r['target_key']}" for r in relationships]
        vectors = self.embedder.embed(texts)
        with self._lock:
            self._ensure(ENTITY_COLLECTION, len(vectors[0]))
            self.client.upsert(
                ENTITY_COLLECTION,
                points=[
                    models.PointStruct(
                        id=point_id(run_id, "rel", r["source_key"], r["relation"].lower(), r["target_key"], r["source_id"]),
                        vector=vec,
                        payload={**r, "run_id": run_id, "kind": "relationship"},
                    )
                    for r, vec in zip(relationships, vectors, strict=True)
                ],
            )

    def load_entity_records(self, run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(entity payloads, relationship payloads) stored for the run."""
        with self._lock:
            if not self.client.collection_exists(ENTITY_COLLECTION):
                return [], []
            entities, relationships, offset = [], [], None
            while True:
                points, offset = self.client.scroll(
                    ENTITY_COLLECTION, scroll_filter=_run_filter(run_id), limit=256, offset=offset, with_payload=True
                )
                for p in points:
                    (entities if (p.payload or {}).get("kind") == "entity" else relationships).append(dict(p.payload or {}))
                if offset is None:
                    return entities, relationships
