"""Routes each sub-task to the researcher agent for its kind (web, document, entity)."""
from collections.abc import Awaitable, Callable

from agents.doc_reader import DocumentReaderAgent
from agents.entity_extractor import EntityExtractorAgent
from agents.web_search import WebSearchAgent
from graph.run_context import current_run_id
from schemas import SourceRef, SubTask
from tools.search import SearchProvider, SearchResult, get_search_provider


class LazySearchProvider:
    """Builds the real provider on first use, so runs that never search need no search API key."""

    def __init__(self):
        self._inner: SearchProvider | None = None

    async def search(self, query: str, num: int = 5) -> list[SearchResult]:
        if self._inner is None:
            self._inner = get_search_provider()
        return await self._inner.search(query, num)


def make_researcher(
    web: WebSearchAgent, docs: DocumentReaderAgent, entities: EntityExtractorAgent
) -> Callable[[SubTask], Awaitable[list[SourceRef]]]:
    async def researcher(task: SubTask) -> list[SourceRef]:
        run_id = current_run_id.get()
        if task.researcher == "document":
            if run_id is None:
                raise RuntimeError("document research needs a run id")
            return await docs.run(run_id, task)
        if task.researcher == "entity":
            if run_id is None:
                raise RuntimeError("entity research needs a run id")
            return await entities.run(run_id, task)
        return await web.run(task)

    return researcher
