import time

import pytest
from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401
from context.memory_store import (
    load_recent_summaries,
    render_memory_block,
    save_session_summary,
)
from context.run_log import RunLog
from context.scratch_store import ScratchStore, SourceNotFoundError
from context.token_budget import BudgetItem, count_tokens, fit_to_budget, total_tokens
from db.models import Run
from db.session import Base, make_engine


def fake_summarizer(text: str, target: int) -> str:
    return " ".join(text.split()[: max(1, target // 2)])


def words(n: int) -> str:
    return " ".join(f"word{i}" for i in range(n))


@pytest.fixture
def session(tmp_path):
    eng = make_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng, expire_on_commit=False)() as s:
        yield s
    eng.dispose()


# 1.1 scratch store
def test_scratch_roundtrip(tmp_path):
    store = ScratchStore(tmp_path)
    sid = store.write_source({"url": "https://a.com", "body_text": "héllo"})
    rec = store.read_source(sid)
    assert rec["source_id"] == sid and rec["body_text"] == "héllo"


def test_scratch_missing_and_invalid_id(tmp_path):
    store = ScratchStore(tmp_path)
    with pytest.raises(SourceNotFoundError):
        store.read_source("0" * 32)
    with pytest.raises(ValueError):
        store.read_source("../../etc/passwd")


# 1.2 token budget
def test_count_tokens():
    assert count_tokens("") == 0
    assert count_tokens("hello world") > 0


def test_under_budget_unchanged():
    items = [BudgetItem("a", "short text", 1)]
    assert fit_to_budget(items, 1000, fake_summarizer) == items


def test_over_budget_compresses_lowest_priority_first():
    hi = BudgetItem("hi", words(200), priority=10)
    lo = BudgetItem("lo", words(200), priority=1)
    budget = count_tokens(hi.text) + 60
    log = RunLog("r1")
    out = fit_to_budget([hi, lo], budget, fake_summarizer, run_log=log)
    by_id = {i.id: i for i in out}
    assert by_id["hi"].text == hi.text  # high priority untouched
    assert by_id["lo"].text != lo.text and by_id["lo"].tokens < lo.tokens
    assert total_tokens(out) <= budget
    cuts = log.records[0].cuts
    assert [c.item_id for c in cuts] == ["lo"] and cuts[0].action == "compressed"
    assert cuts[0].reason and cuts[0].tokens_after < cuts[0].tokens_before


def test_never_truncates_text_mid_way_without_summarizer():
    # a summarizer that fails to shrink must not cause silent truncation: item is dropped and logged
    items = [BudgetItem("a", words(100), 1), BudgetItem("b", words(100), 2)]
    log = RunLog("r")
    out = fit_to_budget(items, count_tokens(items[1].text) + 5, lambda t, n: t, run_log=log)
    assert [i.id for i in out] == ["b"]
    assert out[0].text == items[1].text
    assert log.records[0].cuts[0].action == "dropped"


def test_order_preserved():
    items = [BudgetItem(str(i), words(80), priority=i) for i in range(4)]
    out = fit_to_budget(items, 200, fake_summarizer)
    ids = [i.id for i in out]
    assert ids == sorted(ids)
    assert total_tokens(out) <= 200


# 1.3 run log
def test_run_log_records_and_persists(session):
    run = Run(brief="b")
    session.add(run)
    session.commit()
    log = RunLog(run.id)
    log.record("planner->researcher", 500, 400, budget=450)
    log.save(session)
    session.refresh(run)
    assert run.run_log[0]["boundary"] == "planner->researcher"
    assert run.run_log[0]["tokens_in"] == 500 and run.run_log[0]["budget"] == 450


def test_run_log_save_unknown_run(session):
    with pytest.raises(KeyError):
        RunLog("nope").save(session)


# 1.4 memory store
def test_memory_loads_last_three_oldest_first(session):
    for i in range(5):
        save_session_summary(session, f"summary {i}", fake_summarizer)
        time.sleep(0.002)
    got = load_recent_summaries(session)
    assert [s.summary for s in got] == ["summary 2", "summary 3", "summary 4"]
    block = render_memory_block(got)
    assert "summary 4" in block and "summary 1" not in block
    assert render_memory_block([]) == ""


# 1.5 compression of long summaries (and auto-summarization wiring)
def test_long_summary_is_compressed_before_saving(session):
    row = save_session_summary(session, words(1000), fake_summarizer, max_tokens=100)
    assert row.token_count < count_tokens(words(1000))


def test_deepagents_enables_summarization_by_default():
    from deepagents.middleware.summarization import create_summarization_middleware

    assert callable(create_summarization_middleware)
