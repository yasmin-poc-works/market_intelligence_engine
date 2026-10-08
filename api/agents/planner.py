"""Planner: turns a brief into a typed TaskGraph (3 to 6 sub-tasks) or asks exactly
one clarifying question when the brief is too vague."""
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from config import get_settings
from llm import call_llm, make_chat_model
from schemas import ResearchBrief, SubTask, TaskGraph

MIN_SUBTASKS = 3
MIN_BRIEF_WORDS = 4
DEFAULT_QUESTION = (
    "Which company, market or topic should I research, and what do you want to learn about it?"
)

PLANNER_PROMPT = """You are the research planner for a market intelligence engine.

First use the write_todos tool to draft your plan, then return the final structured answer.

If the brief is too vague to research (no identifiable company, market or topic, or no
clear question), set clarifying_question to exactly ONE question and leave subtasks empty.
Otherwise leave clarifying_question empty and return 3 to {max_subtasks} subtasks.

Each subtask is an independent, non-overlapping research angle with a short title, a concrete
scope (what to find out), and a researcher: "web" (news, filings, articles), "document"
(uploaded PDFs; only if documents were provided) or "entity" (companies, people, funding, figures).
Never include more than {max_subtasks} subtasks."""


class PlannedSubTask(BaseModel):
    title: str
    scope: str
    researcher: Literal["web", "document", "entity"] = "web"


class PlannerOutput(BaseModel):
    clarifying_question: str | None = None
    subtasks: list[PlannedSubTask] = Field(default_factory=list)


class PlanError(RuntimeError):
    pass


@dataclass
class PlanResult:
    task_graph: TaskGraph | None = None
    clarifying_question: str | None = None


def build_planner_agent(max_subtasks: int | None = None, model: Any = None):
    """Deep agent with write_todos (built in) and structured PlannerOutput."""
    from deepagents import create_deep_agent
    from langchain.agents.structured_output import ToolStrategy

    cap = max_subtasks or get_settings().max_parallel_subtasks
    return create_deep_agent(
        model=model or make_chat_model("planner"),
        system_prompt=PLANNER_PROMPT.format(max_subtasks=cap),
        response_format=ToolStrategy(PlannerOutput),  # Groq rejects JSON mode together with tools
    )


def is_vague(text: str) -> bool:
    return len(text.split()) < MIN_BRIEF_WORDS


def _one_question(question: str) -> str:
    q = question.strip()
    first = q.find("?")
    return q[: first + 1] if first != -1 else q


def _build_prompt(brief: ResearchBrief, memory_block: str, feedback: str | None) -> str:
    parts = []
    if memory_block:
        parts.append(memory_block)
    parts.append(f"Research brief:\n{brief.text}")
    if brief.clarification:
        parts.append(f"User clarification: {brief.clarification}\nDo not ask another question; produce the plan.")
    parts.append(
        "Uploaded documents are available." if brief.document_ids and brief.include_documents
        else "No documents were uploaded; do not use the document researcher."
    )
    if not brief.include_web:
        parts.append("Web search is disabled; avoid the web researcher.")
    if feedback:
        parts.append(feedback)
    return "\n\n".join(parts)


async def _ask(agent, prompt: str) -> PlannerOutput:
    result = await call_llm(lambda: agent.ainvoke({"messages": [("user", prompt)]}))
    out = result["structured_response"]
    return out if isinstance(out, PlannerOutput) else PlannerOutput.model_validate(out)


async def plan_brief(
    brief: ResearchBrief,
    *,
    memory_block: str = "",
    agent: Any = None,
    max_subtasks: int | None = None,
) -> PlanResult:
    cap = min(max_subtasks or get_settings().max_parallel_subtasks, 6)

    if not brief.clarification and is_vague(brief.text):
        return PlanResult(clarifying_question=DEFAULT_QUESTION)

    agent = agent or build_planner_agent(cap)
    feedback = None
    for attempt in range(2):  # one retry on an unusable answer
        out = await _ask(agent, _build_prompt(brief, memory_block, feedback))

        if out.clarifying_question and not out.subtasks and not brief.clarification:
            return PlanResult(clarifying_question=_one_question(out.clarifying_question))

        if len(out.subtasks) >= MIN_SUBTASKS:
            planned = out.subtasks[:cap]
            has_docs = bool(brief.document_ids) and brief.include_documents
            subtasks = [
                SubTask(
                    id=f"t{i}",
                    title=t.title,
                    scope=t.scope,
                    researcher="web" if t.researcher == "document" and not has_docs else t.researcher,
                )
                for i, t in enumerate(planned, 1)
            ]
            text = brief.text + (f"\n\n{brief.clarification}" if brief.clarification else "")
            return PlanResult(task_graph=TaskGraph(brief=text, subtasks=subtasks))

        feedback = (
            f"Your previous answer had {len(out.subtasks)} subtasks. "
            f"Return between {MIN_SUBTASKS} and {cap} subtasks and no clarifying question."
        )
    raise PlanError("planner did not produce a valid task graph")
