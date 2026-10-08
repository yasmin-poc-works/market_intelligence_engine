import asyncio

import pytest
from sqlalchemy import text

import llm
from config import Settings
from db.session import Base, make_engine
from schemas import SubTask, TaskGraph


def test_model_for_falls_back_to_default():
    s = Settings(_env_file=None, llm_model="base", writer_model="big")
    assert s.model_for("writer") == "big"
    assert s.model_for("planner") == "base"


def test_task_graph_bounds():
    tasks = [SubTask(id=str(i), title="t", scope="s") for i in range(2)]
    with pytest.raises(ValueError):
        TaskGraph(brief="b", subtasks=tasks)


def test_sqlite_wal_and_tables(tmp_path):
    import db.models  # noqa: F401

    eng = make_engine(f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    Base.metadata.create_all(eng)
    with eng.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    expected = {"runs", "task_graph_logs", "reports", "watchlist_items", "alerts", "session_summaries"}
    assert expected <= set(Base.metadata.tables)
    eng.dispose()


def test_qdrant_embedded(tmp_path, monkeypatch):
    import config

    monkeypatch.setenv("QDRANT_PATH", str(tmp_path / "q"))
    monkeypatch.delenv("QDRANT_URL", raising=False)
    config.get_settings.cache_clear()
    config.get_qdrant_client.cache_clear()
    client = config.get_qdrant_client()
    assert client.get_collections().collections == []
    client.close()
    config.get_settings.cache_clear()
    config.get_qdrant_client.cache_clear()


class RateLimit(Exception):
    status_code = 429


def test_backoff_retries_then_succeeds(monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(llm.asyncio, "sleep", no_sleep)
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RateLimit()
        return "ok"

    assert asyncio.run(llm.call_llm(flaky, max_retries=5)) == "ok"
    assert len(calls) == 3


def test_non_429_not_retried():
    async def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        asyncio.run(llm.call_llm(boom, max_retries=3))


def test_tool_call_failure_retried_then_succeeds():
    calls = []

    async def glitchy():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("Error code: 400 - tool_use_failed")
        return "ok"

    assert asyncio.run(llm.call_llm(glitchy)) == "ok"


def test_tool_call_failure_gives_up_after_limit():
    async def always():
        raise RuntimeError("tool_use_failed")

    with pytest.raises(RuntimeError):
        asyncio.run(llm.call_llm(always))
