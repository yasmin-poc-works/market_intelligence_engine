"""LangGraph state machine:
PLANNING -> RESEARCHING (parallel fan-out) -> join -> SYNTHESIZING -> WRITING -> FACT_CHECKING -> COMPLETE

Typed state at every transition, a join barrier before synthesis, and per-branch failure
handling: one failed researcher is recorded and the run continues; the run fails only if
every researcher fails. Synthesis, writing and fact-checking are injected (Phase 4); the
defaults are pass-through placeholders.
"""
import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agents.planner import PlanResult, plan_brief
from agents.researchers import run_researcher
from graph.events import EventRecorder
from schemas import ResearchBrief, RunStatus, SourceRef, SubTask, TaskGraph


def _merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class GraphState(TypedDict, total=False):
    run_id: str
    brief: ResearchBrief
    memory_block: str
    status: str
    task_graph: TaskGraph | None
    clarifying_question: str | None
    refs: Annotated[list[SourceRef], operator.add]  # fan-in from parallel researchers
    subtask_status: Annotated[dict[str, str], _merge]
    errors: Annotated[list[str], operator.add]
    evidence: Any
    sources: dict[str, dict]
    draft: Any
    entity_graph: Any
    report: Any
    fact_check: Any
    error: str | None


Stage = Callable[[GraphState], Awaitable[dict[str, Any]]]


async def _passthrough(state: GraphState) -> dict[str, Any]:
    return {}


@dataclass
class PipelineDeps:
    recorder: EventRecorder
    planner: Callable[..., Awaitable[PlanResult]] = plan_brief
    researcher: Callable[[SubTask], Awaitable[list[SourceRef]]] = run_researcher
    synthesize: Stage = _passthrough
    write: Stage = _passthrough
    fact_check: Stage = _passthrough
    extra: dict[str, Any] = field(default_factory=dict)


def build_graph(deps: PipelineDeps):
    rec = deps.recorder

    async def plan(state: GraphState) -> dict[str, Any]:
        run_id = state["run_id"]
        rec.set_status(run_id, RunStatus.PLANNING)
        try:
            result = await deps.planner(state["brief"], memory_block=state.get("memory_block", ""))
        except Exception as exc:
            rec.set_status(run_id, RunStatus.FAILED, str(exc))
            return {"status": RunStatus.FAILED, "error": str(exc)}
        if result.clarifying_question:
            rec.record(run_id, "clarification_requested", payload={"question": result.clarifying_question})
            rec.set_status(run_id, RunStatus.NEEDS_CLARIFICATION)
            return {"status": RunStatus.NEEDS_CLARIFICATION, "clarifying_question": result.clarifying_question}
        graph = result.task_graph
        rec.record(run_id, "task_graph_created", payload=graph.model_dump())
        rec.set_status(run_id, RunStatus.RESEARCHING)
        return {
            "status": RunStatus.RESEARCHING,
            "task_graph": graph,
            "subtask_status": {t.id: "pending" for t in graph.subtasks},
        }

    def after_plan(state: GraphState):
        if state.get("status") != RunStatus.RESEARCHING:
            return END
        return [Send("research", {"run_id": state["run_id"], "subtask": t}) for t in state["task_graph"].subtasks]

    async def research(payload: dict[str, Any]) -> dict[str, Any]:
        run_id, task = payload["run_id"], payload["subtask"]
        rec.record(run_id, "subtask_started", task.id, {"title": task.title})
        try:
            refs = await deps.researcher(task)
        except Exception as exc:  # isolate branch failures
            rec.record(run_id, "subtask_failed", task.id, {"error": str(exc)})
            return {"subtask_status": {task.id: "failed"}, "errors": [f"{task.id}: {exc}"]}
        rec.record(run_id, "subtask_done", task.id, {"refs": len(refs)})
        return {"refs": refs, "subtask_status": {task.id: "done"}}

    async def join(state: GraphState) -> dict[str, Any]:
        statuses = state["subtask_status"].values()
        if all(s == "failed" for s in statuses):
            msg = "all research tasks failed: " + "; ".join(state.get("errors", []))
            rec.set_status(state["run_id"], RunStatus.FAILED, msg)
            return {"status": RunStatus.FAILED, "error": msg}
        return {}

    def after_join(state: GraphState):
        return END if state.get("status") == RunStatus.FAILED else "synthesize"

    def stage(name: RunStatus, fn: Stage):
        async def node(state: GraphState) -> dict[str, Any]:
            rec.set_status(state["run_id"], name)
            try:
                update = await fn(state)
            except Exception as exc:
                rec.set_status(state["run_id"], RunStatus.FAILED, str(exc))
                return {"status": RunStatus.FAILED, "error": str(exc)}
            return {**update, "status": name}

        return node

    def guard(next_node: str):
        return lambda state: END if state.get("status") == RunStatus.FAILED else next_node

    async def complete(state: GraphState) -> dict[str, Any]:
        if state.get("report") is not None:
            rec.save_report(state["run_id"], state["report"], state.get("fact_check"))
        rec.set_status(state["run_id"], RunStatus.COMPLETE)
        return {"status": RunStatus.COMPLETE}

    g = StateGraph(GraphState)
    g.add_node("plan", plan)
    g.add_node("research", research)
    g.add_node("join", join)
    g.add_node("synthesize", stage(RunStatus.SYNTHESIZING, deps.synthesize))
    g.add_node("write", stage(RunStatus.WRITING, deps.write))
    g.add_node("fact_check", stage(RunStatus.FACT_CHECKING, deps.fact_check))
    g.add_node("complete", complete)

    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", after_plan, ["research", END])
    g.add_edge("research", "join")  # barrier: join runs once every branch has finished
    g.add_conditional_edges("join", after_join, ["synthesize", END])
    g.add_conditional_edges("synthesize", guard("write"), ["write", END])
    g.add_conditional_edges("write", guard("fact_check"), ["fact_check", END])
    g.add_conditional_edges("fact_check", guard("complete"), ["complete", END])
    g.add_edge("complete", END)
    return g.compile()


async def run_pipeline(
    run_id: str, brief: ResearchBrief, deps: PipelineDeps, memory_block: str = ""
) -> GraphState:
    graph = build_graph(deps)
    return await graph.ainvoke(
        {"run_id": run_id, "brief": brief, "memory_block": memory_block, "refs": [], "errors": []}
    )
