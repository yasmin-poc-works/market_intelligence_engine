"""Source reuse and cleanup across runs.

A brief that is the same topic as (or a follow-up to) the most recent run builds on that run's
sources. A brief on an unrelated topic clears the old runs' stored data first. Database rows
(runs, events, run_log, reports) are never deleted: they are the history and the evidence.

Stored data of a run: web pages in the scratch store (shared by id, so a file is deleted only when
no kept run lists it), uploaded PDFs in `uploads/<run_id>/`, and passages/entities in Qdrant.
"""
import logging
import math
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from agents.fact_checker import Embedder
from agents.planner import is_vague
from agents.synthesis import domain_of
from context.scratch_store import ScratchStore, SourceNotFoundError
from context.vector_store import VectorStore
from db.models import Report, Run
from graph.events import EventRecorder
from schemas import ResearchBrief, SourceRef
from services.uploads import copy_documents

log = logging.getLogger("insightforge")

MAX_SEED_SOURCES = 15  # keeps reused pages from crowding out the new run's own research
MAX_DOCUMENTS = 10
TERMINAL = {"COMPLETE", "FAILED", "NEEDS_CLARIFICATION"}
FINISHED = {"COMPLETE", "FAILED"}


@dataclass
class Related:
    run_id: str
    score: float
    brief: str


@dataclass
class Purged:
    files: int = 0
    uploads: int = 0
    points: int = 0
    runs: int = 0

    def __bool__(self) -> bool:
        return bool(self.files or self.uploads or self.points)


@dataclass
class Prepared:
    seeds: list[SourceRef] = field(default_factory=list)
    brief: ResearchBrief | None = None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class Retention:
    def __init__(
        self,
        session_factory,
        scratch: ScratchStore,
        upload_dir: Path,
        vectors: VectorStore,
        embedder: Callable[[], Embedder],
        *,
        hours: float = 24,
        threshold: float = 0.45,
        reuse: bool = True,
    ):
        self._sf = session_factory
        self._scratch = scratch
        self._uploads = upload_dir
        self._vectors = vectors
        self._embedder = embedder
        self.hours = hours
        self.threshold = threshold
        self.reuse = reuse

    # ---------- which earlier run is this brief about? ----------
    def find_related(self, brief_text: str, exclude_run_id: str) -> Related | None:
        """Best match among recent finished runs that still hold sources, if it is close enough."""
        cutoff = datetime.now(UTC) - timedelta(hours=self.hours)
        with self._sf() as db:
            runs = db.scalars(select(Run).order_by(Run.created_at.desc()).limit(25)).all()
            candidates = [
                r
                for r in runs
                if r.id != exclude_run_id
                and r.status in FINISHED  # a run that only asked a question researched nothing
                and _aware(r.created_at) >= cutoff
                and not (r.options or {}).get("data_purged")
                and ((r.options or {}).get("source_ids") or (r.options or {}).get("document_ids"))
            ]
            if not candidates:
                return None
            prev = candidates[0]  # the most recent one: a follow-up refers to the last thing researched
            texts = [prev.brief]
            report = db.scalars(select(Report).where(Report.run_id == prev.id)).first()
            if report is not None:
                summary = ((report.data or {}).get("report") or {}).get("executive_summary", "")
                texts.append(f"{report.title}. {summary}".strip())
            prev_id, prev_brief = prev.id, prev.brief
        embedder = self._embedder()
        new_vec, *old_vecs = embedder.embed([brief_text, *texts])
        score = max(_cosine(new_vec, v) for v in old_vecs)
        return Related(prev_id, score, prev_brief) if score >= self.threshold else None

    # ---------- reuse ----------
    def seed_refs(self, run_id: str) -> list[SourceRef]:
        with self._sf() as db:
            run = db.get(Run, run_id)
            ids = list((run.options or {}).get("source_ids", [])) if run else []
        refs: list[SourceRef] = []
        for source_id in ids[:MAX_SEED_SOURCES]:
            try:
                rec = self._scratch.read_source(source_id)
            except (SourceNotFoundError, ValueError):
                continue
            url = rec.get("url") or ""
            summary = f"{rec.get('title') or url} ({domain_of(url)}): reused from an earlier run"[:300]
            refs.append(SourceRef(source_id=source_id, summary=summary, kind="web", url=url or None))
        return refs

    def reuse_documents(self, from_run: str, to_run: str, brief: ResearchBrief) -> list[str]:
        """Copy the earlier run's PDFs next to this run's own; returns the combined stored names."""
        with self._sf() as db:
            prev = db.get(Run, from_run)
            names = list((prev.options or {}).get("document_ids", [])) if prev else []
        room = max(0, MAX_DOCUMENTS - len(brief.document_ids))
        copied = copy_documents(self._uploads / from_run, self._uploads / to_run, names[:room], rename_clashes=True)
        return [*brief.document_ids, *copied]

    # ---------- cleanup ----------
    def _in_flight(self, db, exclude: str) -> list[Run]:
        return [r for r in db.scalars(select(Run)).all() if r.id != exclude and r.status not in TERMINAL]

    def _purge(self, run_ids: list[str], exclude: str, older_than: datetime | None = None) -> Purged:
        """Delete the stored data of these (finished) runs, plus scratch files no kept run needs."""
        out = Purged()
        with self._sf() as db:
            in_flight = self._in_flight(db, exclude)
            # an unfinished run has not recorded its source ids yet: spare anything written since it began
            floor = min((_aware(r.created_at) for r in in_flight), default=None)
            for rid in run_ids:
                run = db.get(Run, rid)
                if run is None or run.status not in TERMINAL or rid == exclude:
                    continue
                folder = self._uploads / rid
                if folder.is_dir():
                    out.uploads += sum(1 for p in folder.iterdir() if p.is_file())
                    shutil.rmtree(folder, ignore_errors=True)
                try:
                    out.points += self._vectors.delete_run(rid)
                except Exception:
                    log.exception("could not remove vector data of run %s", rid)
                run.options = {**(run.options or {}), "data_purged": True}
                out.runs += 1
            db.commit()
            kept: set[str] = set()
            for r in db.scalars(select(Run)).all():
                if not (r.options or {}).get("data_purged"):
                    kept.update((r.options or {}).get("source_ids", []))
        out.files = self._delete_scratch(kept, floor, older_than)
        return out

    def _delete_scratch(self, kept: set[str], floor: datetime | None, older_than: datetime | None) -> int:
        root = self._scratch.root
        if not root.is_dir():
            return 0
        removed = 0
        for path in root.glob("*.json"):
            if path.stem in kept:
                continue
            written = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            if (floor is not None and written >= floor) or (older_than is not None and written >= older_than):
                continue
            path.unlink(missing_ok=True)
            removed += 1
        return removed

    def sweep(self, exclude: str = "") -> Purged:
        """Remove the data of runs older than the retention window (and any unreferenced old pages)."""
        cutoff = datetime.now(UTC) - timedelta(hours=self.hours)
        with self._sf() as db:
            old = [
                r.id
                for r in db.scalars(select(Run)).all()
                if r.status in TERMINAL and _aware(r.created_at) < cutoff and not (r.options or {}).get("data_purged")
            ]
        return self._purge(old, exclude, older_than=cutoff)

    def purge_all_finished(self, exclude: str) -> Purged:
        """Different topic: clear the data of every finished run before this one starts."""
        with self._sf() as db:
            ids = [
                r.id
                for r in db.scalars(select(Run)).all()
                if r.status in TERMINAL and not (r.options or {}).get("data_purged")
            ]
        return self._purge(ids, exclude)

    # ---------- one call at the start of a run ----------
    def prepare(self, run_id: str, brief: ResearchBrief, rec: EventRecorder, continued: bool = False) -> Prepared:
        swept = self.sweep(exclude=run_id)
        if swept:
            rec.record(run_id, "storage_cleaned", payload={"reason": "older than the retention window", **vars(swept)})
        if not self.reuse:
            return Prepared()
        if is_vague(brief.text) and not brief.clarification:
            return Prepared()  # the planner will ask a question first; the topic is decided by the answer
        text = brief.text + (f" {brief.clarification}" if brief.clarification else "")
        related = self.find_related(text, run_id)
        if related is not None:
            seeds = self.seed_refs(related.run_id)
            documents = self.reuse_documents(related.run_id, run_id, brief)
            if documents != brief.document_ids:
                with self._sf() as db:
                    run = db.get(Run, run_id)
                    run.options = {**(run.options or {}), "document_ids": documents}
                    db.commit()
            rec.record(
                run_id,
                "sources_reused",
                payload={
                    "from_run": related.run_id,
                    "similarity": round(related.score, 2),
                    "web_pages": len(seeds),
                    "documents": len(documents) - len(brief.document_ids),
                },
            )
            return Prepared(seeds, brief.model_copy(update={"document_ids": documents}))
        if continued:  # answering a clarifying question never clears data
            return Prepared()
        cleared = self.purge_all_finished(exclude=run_id)
        if cleared:
            rec.record(run_id, "storage_cleaned", payload={"reason": "different topic", **vars(cleared)})
        return Prepared()


def build_retention(session_factory=None) -> Retention:
    """Production wiring: the same stores the pipeline uses (shared embedder, scratch dir, embedded Qdrant)."""
    from config import get_settings
    from db.session import SessionLocal

    s = get_settings()
    vectors = VectorStore()
    return Retention(
        session_factory or SessionLocal,
        ScratchStore(),
        s.upload_dir,
        vectors,
        lambda: vectors.embedder,
        hours=s.retention_hours,
        threshold=s.related_brief_threshold,
        reuse=s.reuse_sources,
    )
