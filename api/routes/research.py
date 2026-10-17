import asyncio
import json
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, UploadFile
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from db.models import Report as ReportRow
from db.models import Run
from deps import DepsFactory, get_deps_factory, get_pdf_fetcher, get_retention, get_session_factory, get_upload_dir
from errors import ApiError
from graph.events import TERMINAL, EventRecorder
from schemas import ResearchBrief
from services.runner import create_run, execute_run
from services.uploads import MAX_FILES, PdfFetcher, copy_documents, parse_pdf_url, save_pdf_links, save_pdfs
from tools.documents import display_name

router = APIRouter(prefix="/research", tags=["research"])

POLL_SECONDS = 0.5


@router.post("/run", status_code=202)
async def start_run(
    background: BackgroundTasks,
    brief: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()] = [],  # noqa: B006
    document_urls: Annotated[list[str], Form()] = [],  # noqa: B006
    reference_urls: Annotated[list[str], Form()] = [],  # noqa: B006
    previous_run_id: Annotated[str | None, Form()] = None,
    include_web: Annotated[bool, Form()] = True,
    include_documents: Annotated[bool, Form()] = True,
    clarification: Annotated[str | None, Form()] = None,
    session_factory=Depends(get_session_factory),
    deps_factory: DepsFactory = Depends(get_deps_factory),
    upload_dir: Path = Depends(get_upload_dir),
    fetch_pdf: PdfFetcher = Depends(get_pdf_fetcher),
    retention=Depends(get_retention),
):
    """Start a research run (multipart form: brief, optional PDFs and/or `document_urls` links to PDFs,
    toggles). Linked PDFs are downloaded by the server and handled like uploads. Returns the run id;
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

    links = [u for u in dict.fromkeys(u.strip() for u in document_urls) if u]
    uploads = [f for f in files if f.filename]
    pages = list(dict.fromkeys(parse_pdf_url(u) for u in reference_urls if u.strip()))
    inherited_docs: list[str] = []
    if previous_run_id:  # answering a clarifying question: keep what the first submission came with
        with session_factory() as db:
            prev = db.get(Run, previous_run_id)
        if prev is None:
            raise ApiError(404, "Previous run not found")
        prev_opts = prev.options or {}
        inherited_docs = list(prev_opts.get("document_ids", []))
        pages = list(dict.fromkeys([*prev_opts.get("reference_urls", []), *pages]))
        links = list(dict.fromkeys([*prev_opts.get("document_urls", []), *links]))
    if len(pages) > MAX_FILES:
        raise ApiError(422, f"At most {MAX_FILES} reference web pages are allowed", "too_many_links")
    # links carried over from the first run are already stored as files; only new ones are downloaded
    new_links = [u for u in links if u not in (prev_opts.get("document_urls", []) if previous_run_id else [])]
    if len(inherited_docs) + len(uploads) + len(new_links) > MAX_FILES:
        raise ApiError(422, f"At most {MAX_FILES} documents (files and links together) are allowed", "too_many_files")

    run_id = create_run(session_factory, parsed, [])
    dest = upload_dir / run_id
    try:
        names = copy_documents(upload_dir / previous_run_id, dest, inherited_docs) if previous_run_id else []
        names += await save_pdfs(uploads, dest, len(names))
        names += await save_pdf_links(new_links, fetch_pdf, dest, len(names))
    except ApiError:
        with session_factory() as db:  # a rejected request must not leave a half-started run behind
            db.delete(db.get(Run, run_id))
            db.commit()
        shutil.rmtree(dest, ignore_errors=True)
        raise
    if names or pages:
        with session_factory() as db:
            run = db.get(Run, run_id)
            run.options = {**run.options, "document_ids": names, "document_urls": links, "reference_urls": pages}
            db.commit()
        parsed = parsed.model_copy(update={"document_ids": names, "reference_urls": pages})

    background.add_task(execute_run, run_id, parsed, deps_factory, session_factory, retention, bool(previous_run_id))
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
            "brief": run.brief,
            "status": run.status,
            "error": run.error,
            "clarifying_question": question,
            "documents": [display_name(n) for n in (run.options or {}).get("document_ids", [])],
            "reference_urls": (run.options or {}).get("reference_urls", []),
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
