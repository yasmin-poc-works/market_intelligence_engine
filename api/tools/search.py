"""Web search behind a small interface (SEARCH_PROVIDER), so Tavily or Brave can be added later."""
from dataclasses import dataclass
from typing import Protocol

import httpx

from config import get_settings

SERPAPI_URL = "https://serpapi.com/search.json"
DEFAULT_RESULTS = 5


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    date: str | None = None  # as shown by the engine, e.g. "May 1, 2026"


class SearchProvider(Protocol):
    async def search(self, query: str, num: int = DEFAULT_RESULTS) -> list[SearchResult]: ...


class SearchError(RuntimeError):
    pass


class SerpApiProvider:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        if not api_key:
            raise SearchError("SERPAPI_API_KEY is not set")
        self._key = api_key
        self._client = client

    async def search(self, query: str, num: int = DEFAULT_RESULTS) -> list[SearchResult]:
        params = {"engine": "google", "q": query, "num": num, "api_key": self._key}
        client = self._client or httpx.AsyncClient(timeout=20)
        try:
            resp = await client.get(SERPAPI_URL, params=params)
        except httpx.HTTPError as exc:
            raise SearchError(f"search request failed: {type(exc).__name__}") from None
        finally:
            if self._client is None:
                await client.aclose()
        if resp.status_code != 200:
            raise SearchError(f"search failed with HTTP {resp.status_code}")
        data = resp.json()
        if data.get("error") and not data.get("organic_results"):
            raise SearchError(f"search failed: {data['error']}")
        out: list[SearchResult] = []
        for item in data.get("organic_results", []):
            if item.get("link"):
                out.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=item["link"],
                        snippet=item.get("snippet", ""),
                        date=item.get("date"),
                    )
                )
        return out[:num]


def get_search_provider() -> SearchProvider:
    s = get_settings()
    if s.search_provider == "serpapi":
        return SerpApiProvider(s.serpapi_api_key)
    raise SearchError(f"unknown SEARCH_PROVIDER: {s.search_provider!r}")
