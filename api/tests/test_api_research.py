import json

from db.models import Run, SessionSummary
from deps import get_deps_factory
from main import app
from routes import research
from services.uploads import MAX_BYTES, safe_name
from tests.fake_pipeline import make_deps_factory

PDF = b"%PDF-1.4\n%fake pdf body\n"


def open_stream(api, url, **kw):
    """SSE stream; sse-starlette keeps a module-level event bound to the first event loop,
    and each TestClient request may run on a new one, so reset it (production has one loop)."""
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    return api.client.stream("GET", url, **kw)


def sse_events(response) -> list[tuple[str, str, dict]]:
    """Parse an SSE stream into (id, event, data) tuples."""
    out, cur = [], {}
    for line in response.iter_lines():
        if line.startswith("id:"):
            cur["id"] = line[3:].strip()
        elif line.startswith("event:"):
            cur["event"] = line[6:].strip()
        elif line.startswith("data:"):
            cur["data"] = json.loads(line[5:].strip())
        elif not line.strip() and "event" in cur:
            out.append((cur["id"], cur["event"], cur["data"]))
            cur = {}
    return out


# ---------- 5.1 start a run ----------
def test_run_completes_and_exposes_report(api):
    resp = api.run_brief()
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    info = api.client.get(f"/research/runs/{run_id}").json()
    assert info["status"] == "COMPLETE" and info["report_id"] and info["error"] is None


def test_run_saves_session_summary_for_memory(api):
    api.run_brief()
    with api.sf() as db:
        assert db.query(SessionSummary).count() == 1


def test_vague_brief_returns_clarifying_question(api):
    run_id = api.run_brief("AI stuff").json()["run_id"]
    info = api.client.get(f"/research/runs/{run_id}").json()
    assert info["status"] == "NEEDS_CLARIFICATION" and info["clarifying_question"]
    assert info["report_id"] is None


def test_clarification_answer_lets_the_run_proceed(api):
    run_id = api.run_brief("AI stuff", clarification="Generative AI chip startups in 2026").json()["run_id"]
    assert api.client.get(f"/research/runs/{run_id}").json()["status"] == "COMPLETE"


def test_failed_research_marks_run_failed(api):
    app.dependency_overrides[get_deps_factory] = lambda: make_deps_factory(api.tmp_path, fail_research=True)
    run_id = api.run_brief().json()["run_id"]
    info = api.client.get(f"/research/runs/{run_id}").json()
    assert info["status"] == "FAILED" and "search provider down" in info["error"]


def test_crashing_pipeline_never_stays_in_progress(api):
    def boom(_rec):
        raise RuntimeError("wiring broke")

    app.dependency_overrides[get_deps_factory] = lambda: boom
    run_id = api.run_brief().json()["run_id"]
    info = api.client.get(f"/research/runs/{run_id}").json()
    assert info["status"] == "FAILED" and "wiring broke" in info["error"]


def test_brief_validation(api):
    for bad in ["", "   ", "x" * 2001]:
        r = api.run_brief(bad)
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert api.client.post("/research/run", data={}).status_code == 422


def test_toggles_and_options_are_stored(api):
    run_id = api.run_brief(include_web="false", include_documents="false").json()["run_id"]
    with api.sf() as db:
        opts = db.get(Run, run_id).options
    assert opts["include_web"] is False and opts["include_documents"] is False


# ---------- 5.1 PDF upload ----------
def test_pdf_upload_is_stored_and_recorded(api):
    files = [("files", ("My Report (1).pdf", PDF, "application/pdf")), ("files", ("b.pdf", PDF, "application/pdf"))]
    run_id = api.run_brief(files=files).json()["run_id"]
    with api.sf() as db:
        docs = db.get(Run, run_id).options["document_ids"]
    assert len(docs) == 2
    stored = sorted(p.name for p in (api.tmp_path / "uploads" / run_id).iterdir())
    assert stored == sorted(docs)
    assert all(d.endswith(".pdf") and "/" not in d and " " not in d for d in docs)


def test_more_than_ten_files_rejected(api):
    files = [("files", (f"{i}.pdf", PDF, "application/pdf")) for i in range(11)]
    r = api.run_brief(files=files)
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many_files"


def test_ten_files_accepted(api):
    files = [("files", (f"{i}.pdf", PDF, "application/pdf")) for i in range(10)]
    assert api.run_brief(files=files).status_code == 202


def test_oversized_file_rejected(api):
    big = PDF + b"0" * MAX_BYTES
    r = api.run_brief(files=[("files", ("big.pdf", big, "application/pdf"))])
    assert r.status_code == 413 and r.json()["error"]["code"] == "file_too_large"


def test_file_exactly_at_limit_accepted(api):
    data = PDF + b"0" * (MAX_BYTES - len(PDF))
    assert api.run_brief(files=[("files", ("ok.pdf", data, "application/pdf"))]).status_code == 202


def test_non_pdf_rejected_even_with_pdf_name(api):
    r = api.run_brief(files=[("files", ("evil.pdf", b"MZ not a pdf", "application/pdf"))])
    assert r.status_code == 415 and r.json()["error"]["code"] == "not_a_pdf"


def test_rejected_upload_does_not_start_the_pipeline(api):
    api.run_brief(files=[("files", ("evil.pdf", b"nope", "application/pdf"))])
    assert api.client.get("/reports").json()["total"] == 0


def test_safe_name_strips_paths_and_odd_characters():
    assert safe_name("../../etc/passwd", 1) == "passwd.pdf"
    assert safe_name("C:\\Users\\me\\Q3 report?.pdf", 1) == "Q3_report.pdf"
    assert safe_name(None, 3) == "document_3.pdf"


# ---------- 5.2 SSE ----------
def test_status_stream_replays_and_ends_at_terminal(api):
    run_id = api.run_brief().json()["run_id"]
    with open_stream(api, f"/research/status/{run_id}") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        events = sse_events(r)
    names = [e[1] for e in events]
    statuses = [e[2]["payload"]["status"] for e in events if e[1] == "status"]
    assert statuses == ["PLANNING", "RESEARCHING", "SYNTHESIZING", "WRITING", "FACT_CHECKING", "COMPLETE"]
    assert "task_graph_created" in names and names.count("subtask_done") == 3
    ids = [int(e[0]) for e in events]
    assert ids == sorted(ids)


def test_status_stream_resumes_after_last_event_id(api):
    run_id = api.run_brief().json()["run_id"]
    with open_stream(api, f"/research/status/{run_id}") as r:
        all_events = sse_events(r)
    cut = all_events[3][0]
    with open_stream(api, f"/research/status/{run_id}", headers={"Last-Event-ID": cut}) as r:
        resumed = sse_events(r)
    assert [e[0] for e in resumed] == [e[0] for e in all_events[4:]]


def test_status_stream_streams_live_until_terminal(api, monkeypatch):
    monkeypatch.setattr(research, "POLL_SECONDS", 0.01)
    from graph.events import EventRecorder

    rec = EventRecorder(api.sf)
    with api.sf() as db:
        run = Run(brief="live")
        db.add(run)
        db.commit()
        run_id = run.id
    rec.set_status(run_id, "PLANNING")
    import threading
    import time

    def finish():
        time.sleep(0.2)
        rec.record(run_id, "subtask_done", "t1")
        rec.set_status(run_id, "COMPLETE")

    threading.Thread(target=finish).start()
    with open_stream(api, f"/research/status/{run_id}") as r:
        events = sse_events(r)
    assert [e[1] for e in events] == ["status", "subtask_done", "status"]


def test_unknown_run_is_404(api):
    for url in ["/research/status/nope", "/research/runs/nope"]:
        r = api.client.get(url)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
