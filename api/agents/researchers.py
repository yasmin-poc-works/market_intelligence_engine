"""Isolated researcher runs. Each sub-task gets a fresh deep agent whose only input is
its own sub-task (no brief history, no sibling tasks) and whose only output is a list
of SourceRef references; raw content stays in the scratch store / Qdrant."""
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, Field

from llm import call_llm, make_chat_model
from schemas import SourceRef, SubTask


class ResearcherOutput(BaseModel):
    refs: list[SourceRef] = Field(default_factory=list)


RESEARCHER_PROMPTS = {
    "web": "You are a web research agent. Search, fetch and store sources using your tools.",
    "document": "You are a document research agent. Retrieve relevant chunks from uploaded documents.",
    "entity": "You are an entity extraction agent. Extract companies, people, products and figures.",
}
COMMON_RULES = (
    "\nStore raw content with your tools; never paste article bodies or documents into your reply. "
    "Return only references: for each source its source_id and a one-line summary (max 300 chars)."
)

# Filled in Phase 2 as researcher tools are implemented: {"web": [...], "document": [...], "entity": [...]}
RESEARCHER_TOOLS: dict[str, Sequence[Any]] = {}

AgentFactory = Callable[[str], Any]


def build_researcher_agent(kind: str, model: Any = None):
    from deepagents import create_deep_agent
    from langchain.agents.structured_output import ToolStrategy

    return create_deep_agent(
        model=model or make_chat_model("researcher"),
        tools=list(RESEARCHER_TOOLS.get(kind, [])),
        system_prompt=RESEARCHER_PROMPTS[kind] + COMMON_RULES,
        response_format=ToolStrategy(ResearcherOutput),  # Groq rejects JSON mode together with tools
    )


def subtask_prompt(subtask: SubTask) -> str:
    """The researcher's entire context: its own sub-task and nothing else."""
    return f"Sub-task: {subtask.title}\nScope: {subtask.scope}"


async def run_researcher(subtask: SubTask, agent_factory: AgentFactory = build_researcher_agent) -> list[SourceRef]:
    agent = agent_factory(subtask.researcher)
    prompt = subtask_prompt(subtask)
    result = await call_llm(lambda: agent.ainvoke({"messages": [("user", prompt)]}))
    out = result["structured_response"]
    out = out if isinstance(out, ResearcherOutput) else ResearcherOutput.model_validate(out)
    return out.refs
