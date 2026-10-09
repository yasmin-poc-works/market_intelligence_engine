"""Persist run status and task-graph events (short write transactions, WAL SQLite).
The SSE endpoint reads these via list_events."""
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from db.models import Report as ReportRow
from db.models import Run, TaskGraphLog
from db.session import SessionLocal

TERMINAL = {"COMPLETE", "FAILED", "NEEDS_CLARIFICATION"}


class EventRecorder:
    def __init__(self, session_factory=SessionLocal):
        self._sf = session_factory

    def record(
        self, run_id: str, event: str, subtask_id: str | None = None, payload: dict[str, Any] | None = None
    ) -> int:
        with self._sf() as db:
            row = TaskGraphLog(run_id=run_id, event=event, subtask_id=subtask_id, payload=payload or {})
            db.add(row)
            db.commit()
            return row.id

    def set_status(self, run_id: str, status: str, error: str | None = None) -> None:
        with self._sf() as db:
            run = db.get(Run, run_id)
            if run is None:
                raise KeyError(f"run not found: {run_id}")
            run.status = status
            run.error = error
            if status in TERMINAL:
                run.completed_at = datetime.now(UTC)
            db.add(TaskGraphLog(run_id=run_id, event="status", payload={"status": status, "error": error}))
            db.commit()

    def get_status(self, run_id: str) -> str | None:
        with self._sf() as db:
            run = db.get(Run, run_id)
            return run.status if run else None

    def list_events(self, run_id: str, after_id: int = 0) -> list[TaskGraphLog]:
        with self._sf() as db:
            return list(
                db.scalars(
                    select(TaskGraphLog)
                    .where(TaskGraphLog.run_id == run_id, TaskGraphLog.id > after_id)
                    .order_by(TaskGraphLog.id)
                )
            )

    def save_run_log(self, run_id: str, run_log) -> None:
        with self._sf() as db:
            run = db.get(Run, run_id)
            if run is None:
                raise KeyError(f"run not found: {run_id}")
            run.run_log = [*(run.run_log or []), *run_log.to_dicts()]
            db.commit()

    def save_report(self, run_id: str, report, fact_check=None) -> str:
        with self._sf() as db:
            row = ReportRow(
                id=report.id,
                run_id=run_id,
                title=report.title[:300],
                markdown=report.markdown,
                data={
                    "report": report.model_dump(mode="json"),
                    "fact_check": fact_check.model_dump(mode="json") if fact_check else None,
                },
            )
            db.merge(row)
            db.commit()
            return row.id
