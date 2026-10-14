"""Full-body fetch and parse of an article, with paywall and login-wall detection.

Pages that need payment or a login are skipped (raising `SkippedSource` with the reason)
and logged, instead of silently feeding a teaser into the report."""
import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import httpx

from services import netguard

log = logging.getLogger("insightforge")

MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 3
FETCH_TIMEOUT = 20.0
MIN_BODY_CHARS = 400  # shorter than this is a teaser, an error page or a wall
USER_AGENT = "Mozilla/5.0 (compatible; InsightForgeBot/0.1; +research)"

WALL_STATUSES = {401, 402, 403, 407, 429, 451}
_WALL_PHRASES = re.compile(
    r"subscribe to (continue|read|unlock)|subscribers? only|sign in to (read|continue|view)|"
    r"log ?in to (read|continue|view)|create a free account|register to (read|continue)|"
    r"become a member to|paywall|already a subscriber|you have reached your (free )?article limit|"
    r"to continue reading",
    re.IGNORECASE,
)


class SkippedSource(Exception):
    """The page cannot be used (paywall, login wall, error status, no article body)."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"{url}: {reason}")
        self.url, self.reason = url, reason


@dataclass
class FetchedPage:
    url: str
    title: str
    text: str
    published_date: datetime | None
    byline: str | None


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value)[:19])
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC)


def parse_html(html: str, url: str) -> FetchedPage:
    """Article body and metadata from raw HTML (trafilatura). Raises SkippedSource on walls/teasers."""
    from trafilatura import bare_extraction

    doc = bare_extraction(html, url=url, with_metadata=True, include_comments=False)
    text = (getattr(doc, "text", "") or "").strip() if doc is not None else ""
    wall_hint = bool(_WALL_PHRASES.search(html[:200_000]))
    if len(text) < MIN_BODY_CHARS:
        if wall_hint:
            raise SkippedSource(url, "paywall or login wall")
        raise SkippedSource(url, "no readable article body")
    if wall_hint and len(text) < MIN_BODY_CHARS * 3 and _WALL_PHRASES.search(text):
        raise SkippedSource(url, "paywall or login wall (truncated article)")
    return FetchedPage(
        url=url,
        title=(getattr(doc, "title", "") or "").strip(),
        text=text,
        published_date=_parse_date(getattr(doc, "date", None)),
        byline=(getattr(doc, "author", None) or None),
    )


async def download_html(url: str, client: httpx.AsyncClient) -> str:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        host = urlparse(current).hostname or ""
        if urlparse(current).scheme not in ("http", "https") or not host:
            raise SkippedSource(url, "not an http(s) link")
        try:
            await netguard.check_public_host(host)
        except Exception as exc:
            raise SkippedSource(url, f"blocked or unresolvable host ({getattr(exc, 'message', exc)})") from None
        try:
            async with client.stream("GET", current, headers={"User-Agent": USER_AGENT}) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    current = urljoin(current, resp.headers["location"])
                    continue
                if resp.status_code in WALL_STATUSES:
                    raise SkippedSource(url, f"access denied (HTTP {resp.status_code}): paywall or login wall")
                if resp.status_code >= 400:
                    raise SkippedSource(url, f"HTTP {resp.status_code}")
                ctype = resp.headers.get("content-type", "")
                if ctype and "html" not in ctype and "text" not in ctype:
                    raise SkippedSource(url, f"not an HTML page ({ctype.split(';')[0]})")
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_HTML_BYTES:
                        break
                return bytes(body).decode(resp.encoding or "utf-8", errors="replace")
        except httpx.HTTPError as exc:
            raise SkippedSource(url, f"download failed ({type(exc).__name__})") from None
    raise SkippedSource(url, "too many redirects")


async def fetch_article(url: str, client: httpx.AsyncClient | None = None) -> FetchedPage:
    """Fetch and parse one article. Raises SkippedSource (already logged) when it cannot be used."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False)
    try:
        html = await download_html(url, client)
        return await asyncio.to_thread(parse_html, html, url)
    except SkippedSource as skip:
        log.warning("skipped source %s", skip)
        raise
    finally:
        if owns:
            await client.aclose()
