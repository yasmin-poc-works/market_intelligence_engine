"""Which run the current researcher belongs to. Researchers receive only their sub-task (so they
cannot see the brief or each other), but the document and entity agents need the run id to find
that run's uploads and to scope their Qdrant data."""
from contextvars import ContextVar

current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
