from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select

from config import get_settings
from db.models import Report as ReportRow
from deps import get_session_factory
from errors import ApiError
from services.export import markdown_to_pdf, slugify
from services.share import create_share_token, decode_share_token

router = APIRouter(tags=["reports"])


def _row(db, report_id: str) -> ReportRow:
    row = db.get(ReportRow, report_id)
    if row is None:
        raise ApiError(404, "Report not found")
    return row


def _detail(row: ReportRow) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "title": row.title,
        "created_at": row.created_at,
        "markdown": row.markdown,
        "report": row.data.get("report"),
        "fact_check": row.data.get("fact_check"),
    }


def _download(content: bytes | str, media_type: str, title: str, ext: str) -> Response:
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{slugify(title)}.{ext}"'},
    )


@router.get("/reports")
def list_reports(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    session_factory=Depends(get_session_factory),
):
    with session_factory() as db:
        total = db.scalar(select(func.count()).select_from(ReportRow))
        rows = db.scalars(select(ReportRow).order_by(ReportRow.created_at.desc()).limit(limit).offset(offset))
        items = [
            {
                "id": r.id,
                "run_id": r.run_id,
                "title": r.title,
                "created_at": r.created_at,
                "fact_check_pass_rate": (r.data.get("fact_check") or {}).get("pass_rate"),
            }
            for r in rows
        ]
        return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/reports/{report_id}")
def get_report(report_id: str, session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        return _detail(_row(db, report_id))


@router.get("/reports/{report_id}/export.md")
def export_markdown(report_id: str, session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        row = _row(db, report_id)
        return _download(row.markdown, "text/markdown; charset=utf-8", row.title, "md")


@router.get("/reports/{report_id}/export.pdf")
def export_pdf(report_id: str, session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        row = _row(db, report_id)
        return _download(markdown_to_pdf(row.markdown), "application/pdf", row.title, "pdf")


@router.post("/reports/{report_id}/share", status_code=201)
def create_share_link(report_id: str, session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        _row(db, report_id)
    token, expires = create_share_token(report_id)
    return {
        "token": token,
        "url": f"{get_settings().frontend_url.rstrip('/')}/share/{token}",
        "expires_at": expires,
    }


@router.get("/share/{token}")
def open_share_link(token: str, session_factory=Depends(get_session_factory)):
    """Public, read-only access to one report through a signed 7-day link."""
    report_id = decode_share_token(token)
    with session_factory() as db:
        return _detail(_row(db, report_id))
