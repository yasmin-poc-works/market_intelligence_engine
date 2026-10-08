import asyncio

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401
from agents.planner import (
    DEFAULT_QUESTION,
    PlanError,
    PlannedSubTask,
    PlannerOutput,
    plan_brief,
)
from agents.researchers import ResearcherOutput, run_researcher, subtask_prompt
from db.models import Run
from db.session import Base, make_engine
from graph.events import EventRecorder
from graph.pipeline import PipelineDeps, run_pipeline
from schemas import RunStatus, SourceRef, SubTask, TaskGraph
from tests.planner_fakes import FakeAgent
from tests.planner_fakes import brief as make_brief

BRIEF = "Compare the top three EV makers on battery supply chain risk"


def planned(n, researcher="web"):
    return [PlannedSubTask(title=f"T{i}", scope=f"scope {i}", researcher=researcher) for i in range(n)]


# ---------- 3.1 / 3.2 planner and typed TaskGraph ----------
def test_valid_plan_builds_typed_task_graph():
    agent = FakeAgent(PlannerOutput(subtasks=planned(4)))
    res = asyncio.run(plan_brief(make_brief(BRIEF), agent=agent))
    assert isinstance(res.task_graph, TaskGraph)
    assert [t.id for t in res.task_graph.subtasks] == ["t1", "t2", "t3", "t4"]
    assert res.clarifying_question is None


def test_memory_block_is_in_planner_prompt():
    agent = FakeAgent(PlannerOutput(subtasks=planned(3)))
    asyncio.run(plan_brief(make_brief(BRIEF), memory_block="Context from previous research sessions:\n1. X", agent=agent))
    assert "previous research sessions" in agent.prompts[0]


def test_subtasks_capped_at_configured_max():
    agent = FakeAgent(PlannerOutput(subtasks=planned(6)))
    res = asyncio.run(plan_brief(make_brief(BRIEF), agent=agent, max_subtasks=4))
    assert len(res.task_graph.subtasks) == 4


def test_cap_never_exceeds_six():
    agent = FakeAgent(PlannerOutput(subtasks=planned(6)))
    res = asyncio.run(plan_brief(make_brief(BRIEF), agent=agent, max_subtasks=20))
    assert len(res.task_graph.subtasks) == 6


def test_too_few_subtasks_retries_with_feedback_then_succeeds():
    agent = FakeAgent(PlannerOutput(subtasks=planned(1)), PlannerOutput(subtasks=planned(3)))
    res = asyncio.run(plan_brief(make_brief(BRIEF), agent=agent))
    assert len(res.task_graph.subtasks) == 3
    assert len(agent.prompts) == 2 and "between 3 and" in agent.prompts[1]


def test_too_few_subtasks_twice_raises():
    agent = FakeAgent(PlannerOutput(subtasks=planned(2)))
    with pytest.raises(PlanError):
        asyncio.run(plan_brief(make_brief(BRIEF), agent=agent))


def test_document_researcher_downgraded_without_documents():
    agent = FakeAgent(PlannerOutput(subtasks=planned(3, "document")))
    res = asyncio.run(plan_brief(make_brief(BRIEF), agent=agent))
    assert {t.researcher for t in res.task_graph.subtasks} == {"web"}


def test_document_researcher_kept_with_documents():
    agent = FakeAgent(PlannerOutput(subtasks=planned(3, "document")))
    res = asyncio.run(plan_brief(make_brief(BRIEF, document_ids=["d1"]), agent=agent))
    assert {t.researcher for t in res.task_graph.subtasks} == {"document"}


def test_accepts_dict_structured_response():
    agent = FakeAgent({"subtasks": [{"title": "a", "scope": "b"}] * 3})
    assert len(asyncio.run(plan_brief(make_brief(BRIEF), agent=agent)).task_graph.subtasks) == 3


# ---------- 3.3 vague brief ----------
def test_short_brief_asks_without_calling_llm():
    agent = FakeAgent()
    res = asyncio.run(plan_brief(make_brief("AI stuff"), agent=agent))
    assert res.clarifying_question == DEFAULT_QUESTION and res.task_graph is None
    assert agent.prompts == []


def test_llm_clarifying_question_returned_as_single_question():
    agent = FakeAgent(PlannerOutput(clarifying_question="Which market? And which year? Also why?"))
    res = asyncio.run(plan_brief(make_brief("Tell me about the semiconductor industry stuff"), agent=agent))
    assert res.clarifying_question == "Which market?"
    assert res.task_graph is None


def test_clarification_answer_forces_a_plan_and_is_not_asked_again():
    agent = FakeAgent(PlannerOutput(subtasks=planned(3)))
    brief = make_brief("AI stuff", clarification="Generative AI chip startups in 2026")
    res = asyncio.run(plan_brief(brief, agent=agent))
    assert res.clarifying_question is None and res.task_graph is not None
    assert "Generative AI chip startups" in res.task_graph.brief
    assert "Do not ask another question" in agent.prompts[0]


def test_llm_asking_again_after_clarification_raises():
    agent = FakeAgent(PlannerOutput(clarifying_question="More?"))
    brief = make_brief("AI stuff", clarification="chips")
    with pytest.raises(PlanError):
        asyncio.run(plan_brief(brief, agent=agent))


# ---------- 3.4 isolated researchers, references only ----------
def test_researcher_prompt_contains_only_its_own_subtask():
    own = SubTask(id="t1", title="Battery costs", scope="cell price trends")
    prompt = subtask_prompt(own)
    assert "Battery costs" in prompt and "cell price trends" in prompt
    assert "t2" not in prompt


def test_run_researcher_gets_fresh_agent_and_returns_refs_only():
    agents = []

    def factory(kind):
        a = FakeAgent(ResearcherOutput(refs=[SourceRef(source_id="a" * 32, summary="one line")]))
        agents.append((kind, a))
        return a

    s1 = SubTask(id="t1", title="One", scope="s1", researcher="web")
    s2 = SubTask(id="t2", title="Two", scope="s2", researcher="entity")
    r1 = asyncio.run(run_researcher(s1, factory))
    asyncio.run(run_researcher(s2, factory))
    assert r1[0].summary == "one line"
    assert [k for k, _ in agents] == ["web", "entity"]  # one isolated agent per sub-task
    assert "Two" not in agents[0][1].prompts[0] and "One" not in agents[1][1].prompts[0]


def test_source_ref_rejects_raw_content_dumps():
    with pytest.raises(ValidationError):
        SourceRef(source_id="a" * 32, summary="x" * 5000)


# ---------- 3.5 / 3.6 pipeline and persistence ----------
@pytest.fixture
def env(tmp_path):
    eng = make_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    Base.metadata.create_all(eng)
    sf = sessionmaker(bind=eng, expire_on_commit=False)
    with sf() as s:
        run = Run(brief="b")
        s.add(run)
        s.commit()
        run_id = run.id
    yield run_id, sf, EventRecorder(sf)
    eng.dispose()


def good_planner(n=3):
    async def planner(brief, memory_block=""):
        return await plan_brief(brief, memory_block=memory_block, agent=FakeAgent(PlannerOutput(subtasks=planned(n))))

    return planner


def ref(i):
    return SourceRef(source_id=f"{i:032x}", summary=f"ref {i}")


def run(coro):
    return asyncio.run(coro)


def test_pipeline_happy_path_parallel_join_and_events(env):
    run_id, sf, rec = env
    active, peak = 0, 0
    seen = {}

    async def researcher(task):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return [ref(int(task.id[1:]))]

    async def synthesize(state):
        seen["refs"] = len(state["refs"])  # join barrier: all branches finished before synthesis
        return {"evidence": "e"}

    deps = PipelineDeps(recorder=rec, planner=good_planner(4), researcher=researcher, synthesize=synthesize)
    final = run(run_pipeline(run_id, make_brief(BRIEF), deps))

    assert final["status"] == RunStatus.COMPLETE
    assert peak >= 2  # researchers ran in parallel
    assert seen["refs"] == 4 and len(final["refs"]) == 4
    assert set(final["subtask_status"].values()) == {"done"}

    with sf() as s:
        r = s.get(Run, run_id)
        assert r.status == "COMPLETE" and r.completed_at is not None
    events = rec.list_events(run_id)
    statuses = [e.payload["status"] for e in events if e.event == "status"]
    assert statuses == ["PLANNING", "RESEARCHING", "SYNTHESIZING", "WRITING", "FACT_CHECKING", "COMPLETE"]
    graph_event = next(e for e in events if e.event == "task_graph_created")
    assert len(graph_event.payload["subtasks"]) == 4
    assert sum(e.event == "subtask_done" for e in events) == 4
    assert [e.id for e in events] == sorted(e.id for e in events)


def test_list_events_after_id(env):
    run_id, sf, rec = env
    a = rec.record(run_id, "x")
    rec.record(run_id, "y")
    assert [e.event for e in rec.list_events(run_id, after_id=a)] == ["y"]


def test_one_failed_branch_does_not_stop_the_run(env):
    run_id, sf, rec = env

    async def researcher(task):
        if task.id == "t2":
            raise RuntimeError("paywall")
        return [ref(1)]

    deps = PipelineDeps(recorder=rec, planner=good_planner(3), researcher=researcher)
    final = run(run_pipeline(run_id, make_brief(BRIEF), deps))
    assert final["status"] == RunStatus.COMPLETE
    assert final["subtask_status"]["t2"] == "failed"
    assert any("paywall" in e for e in final["errors"])
    failed = [e for e in rec.list_events(run_id) if e.event == "subtask_failed"]
    assert failed[0].subtask_id == "t2"


def test_all_branches_failing_fails_the_run(env):
    run_id, sf, rec = env

    async def researcher(task):
        raise RuntimeError("down")

    called = []

    async def synthesize(state):
        called.append(1)
        return {}

    deps = PipelineDeps(recorder=rec, planner=good_planner(3), researcher=researcher, synthesize=synthesize)
    final = run(run_pipeline(run_id, make_brief(BRIEF), deps))
    assert final["status"] == RunStatus.FAILED and not called
    with sf() as s:
        assert s.get(Run, run_id).status == "FAILED"


def test_vague_brief_stops_for_clarification(env):
    run_id, sf, rec = env
    deps = PipelineDeps(recorder=rec)  # real planner; short brief never reaches the LLM
    final = run(run_pipeline(run_id, make_brief("AI stuff"), deps))
    assert final["status"] == RunStatus.NEEDS_CLARIFICATION
    assert final["clarifying_question"] == DEFAULT_QUESTION
    events = rec.list_events(run_id)
    assert any(e.event == "clarification_requested" for e in events)
    assert not any(e.event == "task_graph_created" for e in events)


def test_planner_error_fails_run(env):
    run_id, sf, rec = env

    async def planner(brief, memory_block=""):
        raise PlanError("bad plan")

    final = run(run_pipeline(run_id, make_brief(BRIEF), PipelineDeps(recorder=rec, planner=planner)))
    assert final["status"] == RunStatus.FAILED
    with sf() as s:
        assert s.get(Run, run_id).error == "bad plan"


def test_stage_exception_fails_run_and_skips_later_stages(env):
    run_id, sf, rec = env
    called = []

    async def researcher(task):
        return [ref(1)]

    async def write(state):
        raise RuntimeError("llm down")

    async def fact_check(state):
        called.append(1)
        return {}

    deps = PipelineDeps(recorder=rec, planner=good_planner(3), researcher=researcher, write=write, fact_check=fact_check)
    final = run(run_pipeline(run_id, make_brief(BRIEF), deps))
    assert final["status"] == RunStatus.FAILED and not called
