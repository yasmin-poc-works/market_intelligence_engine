import asyncio
import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, UploadFile
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from db.models import Report as ReportRow
from db.models import Run
from deps import DepsFactory, get_deps_factory, get_session_factory, get_upload_dir
from errors import ApiError
from graph.events import TERMINAL, EventRecorder
from schemas import ResearchBrief
from services.runner import create_run, execute_run
from services.uploads import save_pdfs

router = APIRouter(prefix="/research", tags=["research"])

POLL_SECONDS = 0.5


@router.post("/run", status_code=202)
async def start_run(
    background: BackgroundTasks,
    brief: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()] = [],  # noqa: B006
    include_web: Annotated[bool, Form()] = True,
    include_documents: Annotated[bool, Form()] = True,
    clarification: Annotated[str | None, Form()] = None,
    session_factory=Depends(get_session_factory),
    deps_factory: DepsFactory = Depends(get_deps_factory),
    upload_dir: Path = Depends(get_upload_dir),
):
    """Start a research run (multipart form: brief, optional PDFs, toggles). Returns the run id;
    follow progress on GET /research/status/{run_id}."""
    try:
        parsed = ResearchBrief(
            text=brief.strip(),
            include_web=include_web,
            include_documents=include_documents,
            clarification=(clarification or "").strip() or None,
        )
    except ValidationError as exc:
        raise ApiError(422, exc.errors()[0]["msg"], "validation_error") from None

    run_id = create_run(session_factory, parsed, [])
    names = await save_pdfs(files, upload_dir / run_id)
    if names:
        with session_factory() as db:
            run = db.get(Run, run_id)
            run.options = {**run.options, "document_ids": names}
            db.commit()
        parsed = parsed.model_copy(update={"document_ids": names})

    background.add_task(execute_run, run_id, parsed, deps_factory, session_factory)
    return {"run_id": run_id, "status": "PLANNING"}


@router.get("/runs/{run_id}")
def get_run(run_id: str, session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        run = db.get(Run, run_id)
        if run is None:
            raise ApiError(404, "Run not found")
        report = db.query(ReportRow).filter(ReportRow.run_id == run_id).first()
        rec = EventRecorder(session_factory)
        question = None
        if run.status == "NEEDS_CLARIFICATION":
            asked = [e for e in rec.list_events(run_id) if e.event == "clarification_requested"]
            question = asked[-1].payload.get("question") if asked else None
        return {
            "run_id": run.id,
            "status": run.status,
            "error": run.error,
            "clarifying_question": question,
            "report_id": report.id if report else None,
            "created_at": run.created_at,
            "completed_at": run.completed_at,
        }


@router.get("/status/{run_id}")
async def stream_status(
    run_id: str,
    last_event_id: Annotated[str | None, Header()] = None,
    session_factory=Depends(get_session_factory),
):
    """Server-sent events: every status and sub-task event of the run, replayed from the start
    (or after Last-Event-ID) and then live until the run reaches a terminal state."""
    rec = EventRecorder(session_factory)
    if rec.get_status(run_id) is None:
        raise ApiError(404, "Run not found")
    try:
        last = int(last_event_id) if last_event_id else 0
    except ValueError:
        last = 0

    async def events():
        nonlocal last
        while True:
            batch = rec.list_events(run_id, last)
            for e in batch:
                last = e.id
                yield {
                    "id": str(e.id),
                    "event": e.event,
                    "data": json.dumps(
                        {"subtask_id": e.subtask_id, "payload": e.payload, "at": e.created_at.isoformat()}
                    ),
                }
            if not batch and rec.get_status(run_id) in TERMINAL:
                return
            await asyncio.sleep(POLL_SECONDS)

    return EventSourceResponse(events())
