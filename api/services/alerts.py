"""In-app alerts and Slack-compatible webhook delivery for watchlist changes."""
import asyncio
import logging

import httpx

from db.models import Alert, WatchlistItem
from schemas import DiffSummary

log = logging.getLogger("insightforge")

WEBHOOK_ATTEMPTS = 3
WEBHOOK_TIMEOUT = 10.0
SLACK_LIST_LIMIT = 5


def alert_summary(topic: str, diff: DiffSummary) -> str:
    parts = [
        f"{len(items)} {label}"
        for label, items in (("new", diff.added), ("removed", diff.removed), ("changed", diff.changed))
        if items
    ]
    return f'Material change for "{topic}": ' + ", ".join(parts)


def create_alert(session_factory, item: WatchlistItem, run_id: str, diff: DiffSummary) -> Alert:
    with session_factory() as db:
        alert = Alert(
            watchlist_item_id=item.id,
            run_id=run_id,
            summary=alert_summary(item.topic, diff),
            diff=diff.model_dump(mode="json"),
        )
        db.add(alert)
        db.commit()
        return alert


def slack_payload(alert: Alert, link: str | None = None) -> dict:
    """Slack incoming-webhook body (other Slack-compatible endpoints accept it too)."""
    lines = [f"*{alert.summary}*"]
    for label, key in (("Added", "added"), ("Removed", "removed"), ("Changed", "changed")):
        items = alert.diff.get(key) or []
        if items:
            lines.append(f"*{label}*")
            lines += [f"• {i}" for i in items[:SLACK_LIST_LIMIT]]
            if len(items) > SLACK_LIST_LIMIT:
                lines.append(f"…and {len(items) - SLACK_LIST_LIMIT} more")
    if link:
        lines.append(f"<{link}|Open report>")
    return {"text": "\n".join(lines)}


async def deliver_webhook(
    url: str, payload: dict, *, client: httpx.AsyncClient | None = None, base_delay: float = 1.0
) -> bool:
    """POST the payload, retrying transient failures. Never raises: returns whether it was delivered."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT)
    try:
        for attempt in range(WEBHOOK_ATTEMPTS):
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code < 300:
                    return True
                if resp.status_code < 500 and resp.status_code != 429:
                    log.warning("webhook rejected (%s): not retrying", resp.status_code)
                    return False
            except httpx.HTTPError as exc:
                log.warning("webhook attempt %d failed: %s", attempt + 1, exc)
            if attempt < WEBHOOK_ATTEMPTS - 1:
                await asyncio.sleep(base_delay * 2**attempt)
        return False
    finally:
        if owns:
            await client.aclose()
