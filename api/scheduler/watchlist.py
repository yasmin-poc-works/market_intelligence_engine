"""APScheduler runner: one interval job per active watchlist item, in the API process.

Each job runs the normal research pipeline for the item's topic, diffs the new report against
the previous one for that item, and raises an in-app alert (plus a webhook, if configured) on a
material change. Run the API with a single worker so jobs never fire twice.
"""
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from agents.diff import Judge, diff_reports
from config import get_settings
from db.models import Report as ReportRow
from db.models import Run, WatchlistItem
from schemas import Report, ResearchBrief
from services.alerts import create_alert, deliver_webhook, slack_payload
from services.runner import create_run, execute_run

log = logging.getLogger("insightforge")

INTERVAL = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
MISFIRE_GRACE_SECONDS = 3600


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt  # SQLite drops tz


def previous_report(db, item_id: str, exclude_run_id: str) -> Report | None:
    row = db.scalars(
        select(ReportRow)
        .join(Run, Run.id == ReportRow.run_id)
        .where(Run.watchlist_item_id == item_id, ReportRow.run_id != exclude_run_id)
        .order_by(ReportRow.created_at.desc())
        .limit(1)
    ).first()
    return Report.model_validate(row.data["report"]) if row else None


class WatchlistScheduler:
    def __init__(self, session_factory, deps_factory, judge: Judge | None = None, notify=deliver_webhook):
        self._sf = session_factory
        self._deps_factory = deps_factory
        self._judge = judge
        self._notify = notify
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    # ----- lifecycle -----
    def start(self) -> None:
        self.scheduler.start()
        with self._sf() as db:
            for item in db.scalars(select(WatchlistItem).where(WatchlistItem.active.is_(True))):
                self.sync(item)

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def sync(self, item: WatchlistItem) -> None:
        """Create, move or remove the item's job so it matches the item's current settings."""
        if not item.active:
            self.remove(item.id)
            return
        every = INTERVAL[item.frequency]
        now = datetime.now(UTC)
        first = _aware(item.next_run) or now + every
        self.scheduler.add_job(
            self.run_item,
            IntervalTrigger(seconds=int(every.total_seconds()), start_date=max(first, now), timezone="UTC"),
            args=[item.id],
            id=item.id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
        )

    def remove(self, item_id: str) -> None:
        if self.scheduler.get_job(item_id):
            self.scheduler.remove_job(item_id)

    # ----- the job -----
    async def run_item(self, item_id: str) -> str | None:
        """Run the pipeline for one item. Returns the run id (None if the item vanished or is paused)."""
        with self._sf() as db:
            item = db.get(WatchlistItem, item_id)
            if item is None or not item.active:
                return None
            db.expunge(item)
        started = datetime.now(UTC)
        brief = ResearchBrief(text=item.topic[:2000])
        run_id = create_run(self._sf, brief, [])
        with self._sf() as db:
            db.get(Run, run_id).watchlist_item_id = item.id
            db.commit()

        await execute_run(run_id, brief, self._deps_factory, self._sf)
        try:
            await self._after_run(item, run_id)
        except Exception:
            log.exception("post-run diff/alert failed for watchlist item %s", item.id)
        finally:
            with self._sf() as db:
                row = db.get(WatchlistItem, item.id)
                if row is not None:
                    row.last_run = started
                    row.next_run = datetime.now(UTC) + INTERVAL[row.frequency]
                    db.commit()
        return run_id

    async def _after_run(self, item: WatchlistItem, run_id: str) -> None:
        with self._sf() as db:
            row = db.scalars(select(ReportRow).where(ReportRow.run_id == run_id)).first()
            if row is None:  # run failed or needed clarification: nothing to compare
                return
            new = Report.model_validate(row.data["report"])
            old = previous_report(db, item.id, run_id)
        if old is None:  # first run is the baseline
            return
        diff = await diff_reports(old, new, self._judge)
        if not diff.material:
            return
        alert = create_alert(self._sf, item, run_id, diff)
        if item.webhook_url:
            link = f"{get_settings().frontend_url}/reports/{new.id}"
            if not await self._notify(item.webhook_url, slack_payload(alert, link)):
                log.warning("webhook delivery failed for watchlist item %s", item.id)
