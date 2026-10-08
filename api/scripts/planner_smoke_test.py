"""Manual check (not CI): the real planner returns a valid TaskGraph or one question.

Run from api/:  uv run python scripts/planner_smoke_test.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.planner import plan_brief
from schemas import ResearchBrief


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    for text in [
        "Compare the top three EV makers on battery supply chain risk and 2026 outlook",
        "Tell me about the market and what is going on lately in general",
    ]:
        res = await plan_brief(ResearchBrief(text=text))
        print(f"\nBRIEF: {text}")
        if res.clarifying_question:
            print("QUESTION:", res.clarifying_question)
        else:
            for t in res.task_graph.subtasks:
                print(f"  {t.id} [{t.researcher}] {t.title}: {t.scope}")


asyncio.run(main())
