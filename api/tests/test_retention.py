import os
from datetime import UTC, datetime, timedelta

from db.models import Report, Run
from graph.events import EventRecorder
from tests.conftest import make_retention

PDF = b"%PDF-1.4\n%fake pdf body\n"
BRIEF = "Acme funding and EV market outlook"
FOLLOW_UP = "Acme funding and EV market outlook in Europe"
OTHER = "Quantum computing hardware roadmap for superconducting qubits"


def events(api, run_id):
    return {e.event: e.payload for e in EventRecorder(api.sf).list_events(run_id)}


def scratch_files(api):
    folder = api.tmp_path / "scratch"
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def uploads_of(api, run_id):
    folder = api.tmp_path / "uploads" / run_id
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def add_run(api, brief, *, status="COMPLETE", age_hours=0.0, options=None):
    with api.sf() as db:
        run = Run(
            brief=brief,
            status=status,
            options=options or {},
            created_at=datetime.now(UTC) - timedelta(hours=age_hours),
        )
        db.add(run)
        db.commit()
        return run.id


def write_page(api, name="x", age_hours=0.0):
    from context.scratch_store import ScratchStore

    store = ScratchStore(api.tmp_path / "scratch")
    source_id = store.write_source({"url": f"https://{name}.example.com/a", "title": name})
    if age_hours:  # the file's age decides whether the sweep may delete it
        then = (datetime.now(UTC) - timedelta(hours=age_hours)).timestamp()
        os.utime(store.root / f"{source_id}.json", (then, then))
    return source_id


# ---------- deciding whether a brief is about the previous run ----------
def test_finds_the_previous_run_for_a_follow_up(api):
    prev = add_run(api, BRIEF, options={"source_ids": [write_page(api)]})
    related = make_retention(api.sf, api.tmp_path).find_related(FOLLOW_UP, exclude_run_id="new")
    assert related is not None and related.run_id == prev and related.score > 0.45


def test_unrelated_topic_is_not_related(api):
    add_run(api, BRIEF, options={"source_ids": [write_page(api)]})
    assert make_retention(api.sf, api.tmp_path).find_related(OTHER, exclude_run_id="new") is None


def test_no_previous_run_or_no_stored_sources_means_no_match(api):
    ret = make_retention(api.sf, api.tmp_path)
    assert ret.find_related(BRIEF, "new") is None
    add_run(api, BRIEF)  # finished but kept nothing
    assert ret.find_related(BRIEF, "new") is None


def test_a_run_older_than_the_window_or_already_purged_is_ignored(api):
    add_run(api, BRIEF, age_hours=30, options={"source_ids": [write_page(api)]})
    add_run(api, BRIEF, options={"source_ids": ["a" * 32], "data_purged": True})
    assert make_retention(api.sf, api.tmp_path).find_related(BRIEF, "new") is None


# ---------- cleanup ----------
def test_sweep_removes_old_run_data_but_keeps_database_rows(api):
    page = write_page(api, age_hours=30)
    old = add_run(api, BRIEF, age_hours=30, options={"source_ids": [page], "document_ids": ["1_a.pdf"]})
    (api.tmp_path / "uploads" / old).mkdir(parents=True)
    (api.tmp_path / "uploads" / old / "1_a.pdf").write_bytes(PDF)
    fresh = write_page(api, "fresh")
    add_run(api, OTHER, options={"source_ids": [fresh]})

    purged = make_retention(api.sf, api.tmp_path).sweep()

    assert purged.uploads == 1 and purged.files == 1
    assert scratch_files(api) == [fresh] and uploads_of(api, old) == []
    with api.sf() as db:
        assert db.get(Run, old).options["data_purged"] is True  # the run row stays


def test_a_page_shared_with_a_kept_run_survives(api):
    shared = write_page(api)
    add_run(api, BRIEF, age_hours=30, options={"source_ids": [shared]})
    add_run(api, BRIEF, options={"source_ids": [shared]})
    assert make_retention(api.sf, api.tmp_path).sweep().files == 0
    assert scratch_files(api) == [shared]


def test_runs_in_progress_are_never_touched(api):
    running = add_run(api, BRIEF, status="RESEARCHING")
    page = write_page(api)  # written by the running run, ids not recorded yet
    (api.tmp_path / "uploads" / running).mkdir(parents=True)
    (api.tmp_path / "uploads" / running / "1_a.pdf").write_bytes(PDF)
    add_run(api, OTHER, options={"source_ids": [write_page(api, "done")]})

    make_retention(api.sf, api.tmp_path).purge_all_finished(exclude="new")

    assert page in scratch_files(api) and uploads_of(api, running) == ["1_a.pdf"]


# ---------- end to end through the API ----------
def test_follow_up_reuses_sources_and_documents(api):
    first = api.run_brief(BRIEF, files=[("files", ("annual.pdf", PDF, "application/pdf"))]).json()["run_id"]
    first_pages = scratch_files(api)
    assert first_pages and uploads_of(api, first)

    second = api.run_brief(FOLLOW_UP).json()["run_id"]

    reused = events(api, second)["sources_reused"]
    assert reused["from_run"] == first and reused["web_pages"] == len(first_pages) and reused["documents"] == 1
    assert uploads_of(api, second) == ["1_annual.pdf"]
    assert api.client.get(f"/research/runs/{second}").json()["status"] == "COMPLETE"
    assert set(first_pages) <= set(scratch_files(api))  # the earlier pages were kept


def test_unrelated_topic_clears_old_data_first(api):
    first = api.run_brief(BRIEF, files=[("files", ("annual.pdf", PDF, "application/pdf"))]).json()["run_id"]
    old_pages = set(scratch_files(api))

    second = api.run_brief(OTHER).json()["run_id"]

    cleaned = events(api, second)["storage_cleaned"]
    assert cleaned["reason"] == "different topic" and cleaned["files"] == len(old_pages) and cleaned["uploads"] == 1
    assert old_pages.isdisjoint(scratch_files(api)) and uploads_of(api, first) == []
    # the history survives: run, events and report of the first run are still readable
    info = api.client.get(f"/research/runs/{first}").json()
    assert info["status"] == "COMPLETE" and info["report_id"]
    assert api.client.get(f"/reports/{info['report_id']}").status_code == 200
    with api.sf() as db:
        assert db.query(Report).filter_by(run_id=first).count() == 1


def test_answering_a_clarification_never_triggers_cleanup(api):
    first = api.run_brief(BRIEF, files=[("files", ("annual.pdf", PDF, "application/pdf"))]).json()["run_id"]
    pages = set(scratch_files(api))
    second = api.run_brief("AI stuff", clarification=OTHER, previous_run_id=first).json()["run_id"]
    assert "storage_cleaned" not in events(api, second)
    assert pages <= set(scratch_files(api)) and uploads_of(api, first)


def test_reuse_can_be_switched_off(api, monkeypatch):
    from deps import get_retention
    from main import app

    api.run_brief(BRIEF)
    app.dependency_overrides[get_retention] = lambda: make_retention(api.sf, api.tmp_path, reuse=False)
    second = api.run_brief(FOLLOW_UP).json()["run_id"]
    ev = events(api, second)
    assert "sources_reused" not in ev and "storage_cleaned" not in ev


def test_housekeeping_failure_does_not_stop_the_run(api, monkeypatch):
    from deps import get_retention
    from main import app

    class Broken:
        def prepare(self, *a, **k):
            raise RuntimeError("disk on fire")

    app.dependency_overrides[get_retention] = lambda: Broken()
    run_id = api.run_brief(BRIEF).json()["run_id"]
    assert api.client.get(f"/research/runs/{run_id}").json()["status"] == "COMPLETE"


def test_a_vague_brief_clears_nothing_while_the_planner_asks_its_question(api):
    first = api.run_brief(BRIEF).json()["run_id"]
    pages = set(scratch_files(api))
    second = api.run_brief("AI stuff").json()["run_id"]
    assert api.client.get(f"/research/runs/{second}").json()["status"] == "NEEDS_CLARIFICATION"
    assert "storage_cleaned" not in events(api, second) and pages <= set(scratch_files(api))
    assert api.client.get(f"/research/runs/{first}").json()["status"] == "COMPLETE"


def test_a_clarification_answer_about_the_earlier_topic_reuses_its_sources(api):
    first = api.run_brief(BRIEF).json()["run_id"]
    vague = api.run_brief("AI stuff").json()["run_id"]
    third = api.run_brief("AI stuff", clarification=FOLLOW_UP, previous_run_id=vague).json()["run_id"]
    assert events(api, third)["sources_reused"]["from_run"] == first
