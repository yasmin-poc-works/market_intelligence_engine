"""Manual smoke test (not run in CI): a deepagents agent must call a subagent via `task`.

Run from api/:  uv run python scripts/groq_smoke_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage
from langchain_groq import ChatGroq
from langchain_core.tools import tool

from config import get_settings


@tool
def add_numbers(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    s = get_settings()
    model = ChatGroq(model=s.model_for("planner"), api_key=s.groq_api_key, temperature=0)
    agent = create_deep_agent(
        model=model,
        system_prompt=(
            "You are a coordinator. You MUST delegate the arithmetic to the "
            "'calculator' subagent using the task tool, then report its answer."
        ),
        subagents=[
            {
                "name": "calculator",
                "description": "Adds two integers using its add_numbers tool.",
                "system_prompt": "Use the add_numbers tool to answer. Reply with the number only.",
                "tools": [add_numbers],
            }
        ],
    )
    result = agent.invoke({"messages": [("user", "What is 17 + 25? Delegate to the calculator.")]})

    task_calls, tool_calls = [], []
    for m in result["messages"]:
        if isinstance(m, AIMessage):
            for c in m.tool_calls:
                (task_calls if c["name"] == "task" else tool_calls).append(c["name"])
    final = result["messages"][-1].content
    print(f"model={s.model_for('planner')}")
    print(f"task calls={len(task_calls)} other tool calls={tool_calls}")
    print(f"final answer: {final}")
    ok = bool(task_calls) and "42" in str(final)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
