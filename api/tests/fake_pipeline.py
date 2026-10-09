"""A fully faked research pipeline (no network, no LLM) for API tests."""
from agents.fact_checker import make_fact_check_stage
from agents.planner import PlannedSubTask, PlannerOutput, plan_brief
from agents.synthesis import make_synthesis_stage
from agents.writer import DraftSection, WriterOutput, make_write_stage
from context.scratch_store import ScratchStore
from graph.pipeline import PipelineDeps
from schemas import SourceRef
from tests.planner_fakes import FakeAgent
from tests.test_fact_checker import EMB

TOPICS = {
    "Funding": "Acme raised $50 million in Series B funding led by Orbit Capital",
    "Market": "The electric vehicle market grew 12% last year across Europe",
    "Risks": "Battery cell prices fell sharply and supply chains remain concentrated in Asia",
}


def make_deps_factory(tmp_path, fail_research: bool = False):
    store = ScratchStore(tmp_path / "scratch")
    pages: dict[str, str] = {}

    def factory(rec):
        async def planner(brief, memory_block=""):
            plan = PlannerOutput(subtasks=[PlannedSubTask(title=t, scope=f"{t} scope") for t in TOPICS])
            return await plan_brief(brief, agent=FakeAgent(plan))

        async def researcher(task):
            if fail_research:
                raise RuntimeError("search provider down")
            url = f"https://{task.title.lower()}.example.com/post"
            pages[url] = TOPICS[task.title] + "."
            sid = store.write_source(
                {"url": url, "title": task.title, "published_date": "2026-05-01",
                 "body_text": pages[url], "credibility_score": 0.85}
            )
            return [SourceRef(source_id=sid, summary=task.title)]

        async def extractor(chunk):
            return [TOPICS[chunk["title"]]]

        async def generate(prompt):
            return WriterOutput(
                executive_summary="Acme raised $50 million [E1].",
                sections=[
                    DraftSection(heading=t, body=f"{c} [E{i}].") for i, (t, c) in enumerate(TOPICS.items(), 1)
                ],
            )

        async def refetch(url):
            return pages[url]

        return PipelineDeps(
            recorder=rec,
            planner=planner,
            researcher=researcher,
            synthesize=make_synthesis_stage(extractor, lambda t, n: t, store=store, recorder=rec),
            write=make_write_stage(generate),
            fact_check=make_fact_check_stage(EMB, refetch),
        )

    return factory
