"""Structured per-run log of token counts and context cuts at each agent boundary.
This is the evidence for the README's context-blowout example."""
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Cut:
    item_id: str
    action: str  # "compressed" | "dropped"
    reason: str
    tokens_before: int
    tokens_after: int


@dataclass
class BoundaryRecord:
    boundary: str
    tokens_in: int
    tokens_out: int
    budget: int | None = None
    cuts: list[Cut] = field(default_factory=list)
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class RunLog:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.records: list[BoundaryRecord] = []

    def record(
        self,
        boundary: str,
        tokens_in: int,
        tokens_out: int | None = None,
        *,
        budget: int | None = None,
        cuts: list[Cut] | None = None,
    ) -> BoundaryRecord:
        rec = BoundaryRecord(
            boundary=boundary,
            tokens_in=tokens_in,
            tokens_out=tokens_in if tokens_out is None else tokens_out,
            budget=budget,
            cuts=cuts or [],
        )
        self.records.append(rec)
        return rec

    def to_dicts(self) -> list[dict[str, Any]]:
        return [asdict(r) for r in self.records]

    def save(self, session) -> None:
        """Persist onto Run.run_log (short write transaction)."""
        from db.models import Run

        run = session.get(Run, self.run_id)
        if run is None:
            raise KeyError(f"run not found: {self.run_id}")
        run.run_log = self.to_dicts()
        session.commit()
