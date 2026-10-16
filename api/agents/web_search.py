"""Web Search Agent: search the web for one sub-task, fetch the top results in full, score their
credibility and store them in the scratch store. Returns only references (source_id plus a
one-line summary); article bodies never travel between agents.

It works from the sub-task alone (title and scope), never from the whole brief or from siblings.
No LLM call is needed here, which keeps the token cost of research at zero."""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agents.synthesis import domain_of
from config import get_settings
from context.scratch_store import ScratchStore
from schemas import SourceRef, SubTask
from tools.credibility import credibility_score
from tools.fetch import FetchedPage, SkippedSource, fetch_article
from tools.search import DEFAULT_RESULTS, SearchProvider, SearchResult

log = logging.getLogger("insightforge")

MAX_QUERY_CHARS = 180
Fetch = Callable[[str], Awaitable[FetchedPage]]


def build_query(subtask: SubTask) -> str:
    """Search query from the sub-task alone: the planner's short query, else title and scope."""
    if subtask.search_query and subtask.search_query.strip():
        return subtask.search_query.strip()[:MAX_QUERY_CHARS]
    return f"{subtask.title} {subtask.scope}".strip()[:MAX_QUERY_CHARS]


def one_line_summary(page: FetchedPage, hit: SearchResult) -> str:
    snippet = (hit.snippet or page.text[:160]).replace("\n", " ").strip()
    return f"{page.title or hit.title} ({domain_of(page.url)}): {snippet}"[:300]


@dataclass
class WebSearchOutcome:
    refs: list[SourceRef] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (url, reason): paywalls, errors


class WebSearchAgent:
    def __init__(
        self,
        provider: SearchProvider,
        store: ScratchStore | None = None,
        fetch: Fetch = fetch_article,
        top_k: int = DEFAULT_RESULTS,
        max_chars: int | None = None,
    ):
        self._provider = provider
        self._store = store or ScratchStore()
        self._fetch = fetch
        self._top_k = top_k
        self._max_chars = max_chars or get_settings().source_max_chars

    @property
    def store(self) -> ScratchStore:
        return self._store

    async def search_and_store(self, subtask: SubTask) -> WebSearchOutcome:
        hits = await self._provider.search(build_query(subtask), self._top_k)
        outcome = WebSearchOutcome()

        async def one(hit: SearchResult) -> SourceRef | None:
            try:
                page = await self._fetch(hit.url)
            except SkippedSource as skip:
                outcome.skipped.append((skip.url, skip.reason))
                return None
            except Exception as exc:  # one bad page must not sink the sub-task
                log.warning("fetch failed for %s: %s", hit.url, exc)
                outcome.skipped.append((hit.url, f"fetch error ({type(exc).__name__})"))
                return None
            score = credibility_score(domain_of(page.url), page.published_date, page.byline)
            source_id = self._store.write_source(
                {
                    "url": page.url,
                    "title": page.title or hit.title,
                    "published_date": page.published_date.isoformat() if page.published_date else None,
                    "body_text": page.text[: self._max_chars],
                    "credibility_score": score,
                    "byline": page.byline,
                }
            )
            return SourceRef(source_id=source_id, summary=one_line_summary(page, hit), kind="web", url=page.url)

        results = await asyncio.gather(*(one(h) for h in hits[: self._top_k]))
        outcome.refs = [r for r in results if r is not None]
        return outcome

    async def run(self, subtask: SubTask) -> list[SourceRef]:
        outcome = await self.search_and_store(subtask)
        if not outcome.refs:
            reasons = "; ".join(f"{u}: {r}" for u, r in outcome.skipped[:3]) or "the search returned no results"
            raise RuntimeError(f"no usable web sources for '{subtask.title}' ({reasons})")
        return outcome.refs
