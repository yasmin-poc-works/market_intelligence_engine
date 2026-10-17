"""LangGraph state machine:
PLANNING -> RESEARCHING (parallel fan-out) -> join -> SYNTHESIZING -> WRITING -> FACT_CHECKING -> COMPLETE

Typed state at every transition, a join barrier before synthesis, and per-branch failure
handling: one failed researcher is recorded and the run continues; the run fails only if
every researcher fails. Synthesis, writing and fact-checking are injected (Phase 4); the
defaults are pass-through placeholders.
"""
import logging
import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agents.planner import PlanResult, plan_brief
from agents.researchers import run_researcher
from graph.events import EventRecorder
from graph.run_context import current_run_id
from schemas import ResearchBrief, RunStatus, SourceRef, SubTask, TaskGraph

log = logging.getLogger("insightforge")


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
# user-supplied web pages -> (stored refs, [(url, reason it was skipped)])
ReferenceLoader = Callable[[list[str]], Awaitable[tuple[list[SourceRef], list[tuple[str, str]]]]]


async def _no_references(urls: list[str]) -> tuple[list[SourceRef], list[tuple[str, str]]]:
    return [], []


async def _passthrough(state: GraphState) -> dict[str, Any]:
    return {}


@dataclass
class PipelineDeps:
    recorder: EventRecorder
    planner: Callable[..., Awaitable[PlanResult]] = plan_brief
    researcher: Callable[[SubTask], Awaitable[list[SourceRef]]] = run_researcher
    references: ReferenceLoader = _no_references
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
            log.exception("run %s: planning failed", run_id)
            rec.set_status(run_id, RunStatus.FAILED, str(exc))
            return {"status": RunStatus.FAILED, "error": str(exc)}
        if result.clarifying_question:
            rec.record(run_id, "clarification_requested", payload={"question": result.clarifying_question})
            rec.set_status(run_id, RunStatus.NEEDS_CLARIFICATION)
            return {"status": RunStatus.NEEDS_CLARIFICATION, "clarifying_question": result.clarifying_question}
        graph = result.task_graph
        rec.record(run_id, "task_graph_created", payload=graph.model_dump())
        rec.set_status(run_id, RunStatus.RESEARCHING)
        update: dict[str, Any] = {
            "status": RunStatus.RESEARCHING,
            "task_graph": graph,
            "subtask_status": {t.id: "pending" for t in graph.subtasks},
        }
        urls = state["brief"].reference_urls
        if urls:  # pages the user supplied are sources whatever the sub-tasks search for
            try:
                refs, skipped = await deps.references(urls)
            except Exception as exc:
                log.exception("run %s: loading reference pages failed", run_id)
                refs, skipped = [], [(u, f"{type(exc).__name__}") for u in urls]
            rec.record(
                run_id,
                "references_loaded",
                payload={"loaded": len(refs), "skipped": [{"url": u, "reason": r} for u, r in skipped]},
            )
            update["refs"] = refs
        return update

    def after_plan(state: GraphState):
        if state.get("status") != RunStatus.RESEARCHING:
            return END
        return [Send("research", {"run_id": state["run_id"], "subtask": t}) for t in state["task_graph"].subtasks]

    async def research(payload: dict[str, Any]) -> dict[str, Any]:
        run_id, task = payload["run_id"], payload["subtask"]
        rec.record(run_id, "subtask_started", task.id, {"title": task.title})
        current_run_id.set(run_id)
        try:
            refs = await deps.researcher(task)
        except Exception as exc:  # isolate branch failures
            log.exception("run %s: subtask %s failed", run_id, task.id)
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
        web_ids = [r.source_id for r in state.get("refs", []) if r.kind == "web"]
        if web_ids:
            rec.save_source_ids(state["run_id"], web_ids)
        return {}

    def after_join(state: GraphState):
        return END if state.get("status") == RunStatus.FAILED else "synthesize"

    def stage(name: RunStatus, fn: Stage):
        async def node(state: GraphState) -> dict[str, Any]:
            rec.set_status(state["run_id"], name)
            try:
                update = await fn(state)
            except Exception as exc:
                log.exception("run %s: %s stage failed", state["run_id"], name.value)
                rec.set_status(state["run_id"], RunStatus.FAILED, f"{name.value} failed: {exc}")
                return {"status": RunStatus.FAILED, "error": f"{name.value} failed: {exc}"}
            return {**update, "status": name}

        return node

    def guard(next_node: str):
        return lambda state: END if state.get("status") == RunStatus.FAILED else next_node

    async def complete(state: GraphState) -> dict[str, Any]:
        if state.get("report") is not None:
            rec.save_report(
                state["run_id"], state["report"], state.get("fact_check"), state.get("sources"), state.get("evidence")
            )
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
    run_id: str,
    brief: ResearchBrief,
    deps: PipelineDeps,
    memory_block: str = "",
    seed_refs: list[SourceRef] | None = None,
) -> GraphState:
    """seed_refs: sources from an earlier, related run that this run may build on."""
    graph = build_graph(deps)
    return await graph.ainvoke(
        {"run_id": run_id, "brief": brief, "memory_block": memory_block, "refs": list(seed_refs or []), "errors": []}
    )
