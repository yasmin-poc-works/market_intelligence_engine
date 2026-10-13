import asyncio

import httpx
import pytest
from sqlalchemy import select

from agents.diff import diff_findings, is_material, report_findings
from db.models import Alert, Run, WatchlistItem
from schemas import Confidence, DiffSummary, Report, ReportSection
from scheduler import WatchlistScheduler
from services.alerts import deliver_webhook, slack_payload
from tests import fake_pipeline
from tests.fake_pipeline import make_deps_factory


def make_report(summary: str, *bodies: str) -> Report:
    return Report(
        id="r", run_id="x", title="t", executive_summary=summary,
        sections=[ReportSection(heading=f"S{i}", body=b, confidence=Confidence.HIGH) for i, b in enumerate(bodies)],
        created_at="2026-01-01T00:00:00Z",
    )


# ---------- Diff Agent ----------
def test_rephrasing_is_ignored():
    old = make_report("Acme raised $50 million in Series B funding [Source: a.com, 2026-01-01, credibility: 0.85].")
    new = make_report("Acme raised $50 million in Series B funding, led by Orbit Capital [E1].")
    diff = diff_findings(report_findings(old), report_findings(new))
    assert not (diff.added or diff.removed or diff.changed)
    assert not diff.material


def test_changed_figure_is_changed_and_material():
    old = make_report("x", "Acme raised $50 million in Series B funding led by Orbit Capital.")
    new = make_report("x", "Acme raised $80 million in Series B funding led by Orbit Capital.")
    diff = diff_findings(report_findings(old), report_findings(new))
    assert len(diff.changed) == 1 and "$80" in diff.changed[0] and "$50" in diff.changed[0]
    assert not diff.added and not diff.removed and diff.material


def test_added_and_removed_findings():
    old = make_report("x", "Battery cell prices fell sharply during the year.")
    new = make_report("x", "Regulators approved the merger between two European carmakers.")
    diff = diff_findings(report_findings(old), report_findings(new))
    assert diff.added == ["Regulators approved the merger between two European carmakers."]
    assert diff.removed == ["Battery cell prices fell sharply during the year."]


def test_materiality_rules():
    assert not is_material(DiffSummary(added=["A new partnership was announced today"]))
    assert is_material(DiffSummary(added=["Revenue reached 40 percent growth this year"]))
    assert is_material(DiffSummary(added=["one new thing here", "another new thing", "third new thing"]))


# ---------- webhook delivery ----------
def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_webhook_retries_5xx_then_succeeds():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(500 if len(calls) < 3 else 200)

    ok = asyncio.run(deliver_webhook("http://h.test/x", {"text": "hi"}, client=_client(handler), base_delay=0))
    assert ok and len(calls) == 3


def test_webhook_does_not_retry_4xx_and_never_raises():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(404)

    assert not asyncio.run(deliver_webhook("http://h.test/x", {}, client=_client(handler), base_delay=0))
    assert len(calls) == 1

    def boom(req):
        raise httpx.ConnectError("down")

    assert not asyncio.run(deliver_webhook("http://h.test/x", {}, client=_client(boom), base_delay=0))


def test_slack_payload_format():
    alert = Alert(summary="Material change", diff={"added": ["a"] * 7, "removed": [], "changed": ["b -> c"]})
    text = slack_payload(alert, "http://app/reports/1")["text"]
    assert text.startswith("*Material change*")
    assert "…and 2 more" in text
    assert "<http://app/reports/1|Open report>" in text


# ---------- scheduler end to end ----------
@pytest.fixture
def sched(api, tmp_path):
    sent = []

    async def notify(url, payload):
        sent.append((url, payload))
        return True

    s = WatchlistScheduler(api.sf, make_deps_factory(tmp_path), notify=notify)
    s.sent = sent
    yield s
    s.shutdown()


def _item(api, **kw):
    with api.sf() as db:
        item = WatchlistItem(topic="Acme and the EV market", **kw)
        db.add(item)
        db.commit()
        return item.id


def test_first_run_is_baseline_second_run_alerts_and_posts_webhook(api, sched, monkeypatch):
    item_id = _item(api, webhook_url="http://hooks.test/slack")

    assert asyncio.run(sched.run_item(item_id))
    with api.sf() as db:
        assert db.scalars(select(Alert)).first() is None  # baseline: nothing to compare
        item = db.get(WatchlistItem, item_id)
        assert item.last_run and item.next_run
        assert db.scalars(select(Run)).first().watchlist_item_id == item_id

    asyncio.run(sched.run_item(item_id))  # unchanged content: no alert
    with api.sf() as db:
        assert db.scalars(select(Alert)).first() is None
    assert sched.sent == []

    monkeypatch.setitem(
        fake_pipeline.TOPICS, "Funding", "Acme raised $80 million in Series C funding led by Orbit Capital"
    )
    asyncio.run(sched.run_item(item_id))
    with api.sf() as db:
        alert = db.scalars(select(Alert)).one()
    assert alert.watchlist_item_id == item_id and not alert.read
    assert alert.diff["material"] and alert.diff["changed"]
    assert sched.sent[0][0] == "http://hooks.test/slack"
    assert "Material change" in sched.sent[0][1]["text"]

    listed = api.client.get("/alerts", params={"unread_only": True}).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == alert.id
    assert api.client.post(f"/alerts/{alert.id}/read").json()["read"] is True
    assert api.client.get("/alerts", params={"unread_only": True}).json()["total"] == 0
    assert api.client.post("/alerts/nope/read").status_code == 404


def test_paused_or_deleted_item_is_skipped(api, sched):
    paused = _item(api, active=False)
    assert asyncio.run(sched.run_item(paused)) is None
    assert asyncio.run(sched.run_item("missing")) is None


def test_failed_run_still_advances_schedule(api, tmp_path):
    s = WatchlistScheduler(api.sf, make_deps_factory(tmp_path, fail_research=True))
    item_id = _item(api)
    asyncio.run(s.run_item(item_id))
    with api.sf() as db:
        assert db.get(WatchlistItem, item_id).next_run is not None
        assert db.scalars(select(Alert)).first() is None


def test_sync_adds_and_removes_jobs(api, sched):
    async def go():
        sched.scheduler.start()
        item_id = _item(api, frequency="daily")
        with api.sf() as db:
            item = db.get(WatchlistItem, item_id)
        sched.sync(item)
        assert sched.scheduler.get_job(item_id)
        item.active = False
        sched.sync(item)
        assert sched.scheduler.get_job(item_id) is None
        sched.shutdown()  # stop inside the loop that started it
        await asyncio.sleep(0)

    asyncio.run(go())
