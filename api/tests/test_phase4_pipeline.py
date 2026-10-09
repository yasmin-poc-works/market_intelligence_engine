import asyncio

from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401
from agents.fact_checker import make_fact_check_stage
from agents.planner import PlannedSubTask, PlannerOutput, plan_brief
from agents.synthesis import make_synthesis_stage
from agents.writer import DraftSection, WriterOutput, make_write_stage
from context.scratch_store import ScratchStore
from db.models import Report as ReportRow
from db.models import Run
from db.session import Base, make_engine
from graph.events import EventRecorder
from graph.pipeline import PipelineDeps, run_pipeline
from schemas import ResearchBrief, RunStatus, SourceRef
from tests.planner_fakes import FakeAgent
from tests.test_fact_checker import EMB

BODY = {
    "Funding": "Acme raised $50 million in Series B funding led by Orbit Capital.",
    "Market": "The electric vehicle market grew 12% last year across Europe.",
    "Risks": "Battery cell prices fell sharply and supply chains remain concentrated in Asia.",
}
CLAIM = {
    "Funding": "Acme raised $50 million in Series B funding led by Orbit Capital",
    "Market": "The electric vehicle market grew 12% last year across Europe",
    "Risks": "Battery cell prices fell sharply and supply chains remain concentrated in Asia",
}


def test_full_pipeline_brief_to_persisted_fact_checked_report(tmp_path):
    eng = make_engine(f"sqlite:///{(tmp_path / 'e2e.db').as_posix()}")
    Base.metadata.create_all(eng)
    sf = sessionmaker(bind=eng, expire_on_commit=False)
    with sf() as s:
        run = Run(brief="b")
        s.add(run)
        s.commit()
        run_id = run.id
    rec = EventRecorder(sf)
    store = ScratchStore(tmp_path / "scratch")
    pages = {}

    async def planner(brief, memory_block=""):
        plan = PlannerOutput(subtasks=[PlannedSubTask(title=t, scope=f"{t} scope") for t in BODY])
        return await plan_brief(brief, agent=FakeAgent(plan))

    async def researcher(task):
        url = f"https://{task.title.lower()}.example.com/post"
        pages[url] = BODY[task.title]
        sid = store.write_source(
            {"url": url, "title": task.title, "published_date": "2026-05-01", "body_text": BODY[task.title], "credibility_score": 0.85}
        )
        return [SourceRef(source_id=sid, summary=task.title)]

    async def extractor(chunk):
        return [CLAIM[chunk["title"]]]

    ids = {t: i for i, t in enumerate(BODY, 1)}
    out = WriterOutput(
        executive_summary="Acme raised $50 million [E1]. Fabricated trend nobody sourced.",
        sections=[DraftSection(heading=t, body=f"{CLAIM[t]} [E{ids[t]}].") for t in BODY],
    )

    async def generate(prompt):
        return out

    async def refetch(url):
        return pages[url]

    deps = PipelineDeps(
        recorder=rec,
        planner=planner,
        researcher=researcher,
        synthesize=make_synthesis_stage(extractor, lambda t, n: t[: n * 3], store=store, recorder=rec),
        write=make_write_stage(generate),
        fact_check=make_fact_check_stage(EMB, refetch),
    )
    final = asyncio.run(run_pipeline(run_id, ResearchBrief(text="Acme funding and EV market outlook"), deps))

    assert final["status"] == RunStatus.COMPLETE
    report = final["report"]
    assert "Fabricated trend" not in report.markdown  # ungrounded sentence removed by the writer
    assert "[Source: funding.example.com, 2026-05-01, credibility: 0.85]" in report.markdown
    assert final["fact_check"].total_checked == 3 and final["fact_check"].pass_rate == 1.0
    assert "## Fact-check" in report.markdown

    with sf() as s:
        row = s.get(ReportRow, report.id)
        assert row is not None and row.run_id == run_id and row.markdown == report.markdown
        assert row.data["fact_check"]["passed"] == 3
        assert s.get(Run, run_id).run_log[0]["boundary"] == "synthesis->writer"
    eng.dispose()
