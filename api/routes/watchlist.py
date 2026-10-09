from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field
from sqlalchemy import select

from db.models import WatchlistItem
from deps import get_session_factory
from errors import ApiError

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

INTERVAL = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


class WatchlistCreate(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    frequency: Literal["daily", "weekly"] = "weekly"
    webhook_url: AnyHttpUrl | None = None
    active: bool = True


class WatchlistUpdate(BaseModel):
    topic: str | None = Field(default=None, min_length=1, max_length=2000)
    frequency: Literal["daily", "weekly"] | None = None
    webhook_url: AnyHttpUrl | None = None
    active: bool | None = None


class WatchlistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    topic: str
    frequency: str
    webhook_url: str | None
    active: bool
    last_run: datetime | None
    next_run: datetime | None
    created_at: datetime


def _get(db, item_id: str) -> WatchlistItem:
    item = db.get(WatchlistItem, item_id)
    if item is None:
        raise ApiError(404, "Watchlist item not found")
    return item


@router.post("", status_code=201, response_model=WatchlistOut)
def create_item(body: WatchlistCreate, session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        item = WatchlistItem(
            topic=body.topic.strip(),
            frequency=body.frequency,
            webhook_url=str(body.webhook_url) if body.webhook_url else None,
            active=body.active,
            next_run=datetime.now(UTC) + INTERVAL[body.frequency],
        )
        db.add(item)
        db.commit()
        return item


@router.get("", response_model=list[WatchlistOut])
def list_items(session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        return list(db.scalars(select(WatchlistItem).order_by(WatchlistItem.created_at.desc())))


@router.get("/{item_id}", response_model=WatchlistOut)
def get_item(item_id: str, session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        return _get(db, item_id)


@router.patch("/{item_id}", response_model=WatchlistOut)
def update_item(item_id: str, body: WatchlistUpdate, session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        item = _get(db, item_id)
        data = body.model_dump(exclude_unset=True)
        if "topic" in data:
            item.topic = data["topic"].strip()
        if "webhook_url" in data:
            item.webhook_url = str(data["webhook_url"]) if data["webhook_url"] else None
        if data.get("active") is not None:
            item.active = data["active"]
        if data.get("frequency"):
            item.frequency = data["frequency"]
            item.next_run = datetime.now(UTC) + INTERVAL[item.frequency]
        db.commit()
        return item


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: str, session_factory=Depends(get_session_factory)):
    with session_factory() as db:
        db.delete(_get(db, item_id))
        db.commit()
