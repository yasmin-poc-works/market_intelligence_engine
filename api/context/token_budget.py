"""Token counting and budget fitting. Over budget means compressing lower-priority
items by summarizing; content is never arbitrarily truncated."""
from collections.abc import Callable
from dataclasses import dataclass, replace

from context.run_log import Cut, RunLog

Summarizer = Callable[[str, int], str]  # (text, target_tokens) -> shorter text

MIN_SUMMARY_TOKENS = 30

_encoder = None
_encoder_failed = False


def count_tokens(text: str) -> int:
    """tiktoken count; falls back to ~4 chars/token if the encoding is unavailable (offline)."""
    global _encoder, _encoder_failed
    if not text:
        return 0
    if _encoder is None and not _encoder_failed:
        try:
            import tiktoken

            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _encoder_failed = True
    if _encoder is not None:
        return len(_encoder.encode(text, disallowed_special=()))
    return -(-len(text) // 4)


@dataclass(frozen=True)
class BudgetItem:
    id: str
    text: str
    priority: float  # higher = more important, compressed last

    @property
    def tokens(self) -> int:
        return count_tokens(self.text)


def total_tokens(items: list[BudgetItem]) -> int:
    return sum(i.tokens for i in items)


def fit_to_budget(
    items: list[BudgetItem],
    budget: int,
    summarizer: Summarizer,
    *,
    boundary: str = "fit_to_budget",
    run_log: RunLog | None = None,
) -> list[BudgetItem]:
    """Return items whose total tokens fit `budget`, original order preserved.

    1. Compress items lowest priority first, each down to just what is needed
       (never below MIN_SUMMARY_TOKENS).
    2. If still over, drop the lowest-priority items whole (logged as dropped).
    Every change is recorded in the run log with the reason.
    """
    tokens_in = total_tokens(items)
    current = {i.id: i for i in items}
    cuts: list[Cut] = []

    def over() -> int:
        return total_tokens(list(current.values())) - budget

    if over() > 0:
        for item in sorted(items, key=lambda i: i.priority):
            excess = over()
            if excess <= 0:
                break
            before = current[item.id].tokens
            if before <= MIN_SUMMARY_TOKENS:
                continue
            target = max(MIN_SUMMARY_TOKENS, before - excess)
            summary = summarizer(current[item.id].text, target)
            after = count_tokens(summary)
            if after >= before:  # summarizer did not help; try the next item
                continue
            current[item.id] = replace(current[item.id], text=summary)
            cuts.append(Cut(item.id, "compressed", f"over budget by {excess} tokens", before, after))

    for item in sorted(items, key=lambda i: i.priority):
        excess = over()
        if excess <= 0:
            break
        before = current[item.id].tokens
        del current[item.id]
        cuts.append(Cut(item.id, "dropped", f"still over budget by {excess} tokens", before, 0))

    result = [current[i.id] for i in items if i.id in current]
    if run_log is not None:
        run_log.record(boundary, tokens_in, total_tokens(result), budget=budget, cuts=cuts)
    return result
