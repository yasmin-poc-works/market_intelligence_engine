from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update

from db.models import Alert
from deps import get_session_factory
from errors import ApiError

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    watchlist_item_id: str
    run_id: str | None
    summary: str
    diff: dict
    read: bool
    created_at: datetime


@router.get("")
def list_alerts(
    unread_only: bool = False,
    watchlist_item_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    session_factory=Depends(get_session_factory),
):
    limit = max(1, min(limit, 200))
    query = select(Alert)
    if unread_only:
        query = query.where(Alert.read.is_(False))
    if watchlist_item_id:
        query = query.where(Alert.watchlist_item_id == watchlist_item_id)
    with session_factory() as db:
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(query.order_by(Alert.created_at.desc()).limit(limit).offset(max(0, offset)))
        return {"total": total, "items": [AlertOut.model_validate(r) for r in rows]}


@router.post("/read-all")
def mark_all_read(session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        result = db.execute(update(Alert).where(Alert.read.is_(False)).values(read=True))
        db.commit()
        return {"updated": result.rowcount}


@router.post("/{alert_id}/read", response_model=AlertOut)
def mark_read(alert_id: str, session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        alert = db.get(Alert, alert_id)
        if alert is None:
            raise ApiError(404, "Alert not found")
        alert.read = True
        db.commit()
        return alert
