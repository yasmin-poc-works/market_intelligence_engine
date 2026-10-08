"""Cross-session memory: the planner loads only the last N compressed session summaries."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from context.token_budget import Summarizer, count_tokens
from db.models import SessionSummary

MEMORY_SUMMARY_COUNT = 3
MAX_SUMMARY_TOKENS = 300


def save_session_summary(
    db: Session,
    summary: str,
    summarizer: Summarizer,
    run_id: str | None = None,
    max_tokens: int = MAX_SUMMARY_TOKENS,
) -> SessionSummary:
    """Compress (never truncate) an over-long summary, then persist it."""
    if count_tokens(summary) > max_tokens:
        summary = summarizer(summary, max_tokens)
    row = SessionSummary(run_id=run_id, summary=summary, token_count=count_tokens(summary))
    db.add(row)
    db.commit()
    return row


def load_recent_summaries(db: Session, n: int = MEMORY_SUMMARY_COUNT) -> list[SessionSummary]:
    """Last n summaries, oldest first."""
    rows = db.scalars(
        select(SessionSummary)
        .order_by(SessionSummary.created_at.desc(), SessionSummary.id.desc())
        .limit(n)
    ).all()
    return list(reversed(rows))


def render_memory_block(summaries: list[SessionSummary]) -> str:
    if not summaries:
        return ""
    lines = [f"{i}. {s.summary}" for i, s in enumerate(summaries, 1)]
    return "Context from previous research sessions:\n" + "\n".join(lines)
