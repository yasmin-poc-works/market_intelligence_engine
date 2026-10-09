"""Runs one research pipeline to completion (called as a FastAPI background task)."""
import logging

from context.memory_store import load_recent_summaries, render_memory_block, save_session_summary
from db.models import Run
from graph.events import EventRecorder
from graph.pipeline import run_pipeline
from schemas import ResearchBrief, RunStatus

log = logging.getLogger("insightforge")


def create_run(session_factory, brief: ResearchBrief, document_ids: list[str]) -> str:
    with session_factory() as db:
        run = Run(
            brief=brief.text,
            status=RunStatus.PLANNING.value,
            options={
                "include_web": brief.include_web,
                "include_documents": brief.include_documents,
                "document_ids": document_ids,
                "clarification": brief.clarification,
            },
        )
        db.add(run)
        db.commit()
        return run.id


def _lazy_llm_summarizer(text: str, target_tokens: int) -> str:
    from context.summarizer import make_llm_summarizer

    return make_llm_summarizer()(text, target_tokens)


async def execute_run(run_id: str, brief: ResearchBrief, deps_factory, session_factory) -> None:
    rec = EventRecorder(session_factory)
    try:
        with session_factory() as db:
            memory = render_memory_block(load_recent_summaries(db))
        final = await run_pipeline(run_id, brief, deps_factory(rec), memory)
        report = final.get("report")
        if final.get("status") == RunStatus.COMPLETE and report is not None:
            with session_factory() as db:
                save_session_summary(db, report.executive_summary, _lazy_llm_summarizer, run_id=run_id)
    except Exception as exc:  # last-resort guard: a crashed run must never stay "in progress"
        log.exception("run %s crashed", run_id)
        rec.set_status(run_id, RunStatus.FAILED, f"{type(exc).__name__}: {exc}")
