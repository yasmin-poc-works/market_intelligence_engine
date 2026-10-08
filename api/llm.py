"""Concurrency limiting and 429 backoff for LLM calls."""
import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from config import get_settings

T = TypeVar("T")

_semaphore: asyncio.Semaphore | None = None


def get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_settings().llm_max_concurrency)
    return _semaphore


def is_rate_limited(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    return status == 429 or "rate limit" in str(exc).lower()


def is_tool_call_failure(exc: BaseException) -> bool:
    """Groq 400 when the model emits a malformed or unregistered tool call (intermittent)."""
    return "tool_use_failed" in str(exc)


GENERATION_RETRIES = 2


async def call_llm(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int | None = None,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> T:
    """Run an LLM call under the concurrency semaphore, retrying 429s with
    exponential backoff and jitter, and retrying intermittent malformed tool calls a
    couple of times. Other errors propagate immediately."""
    retries = get_settings().llm_max_retries if max_retries is None else max_retries
    attempt = 0
    gen_failures = 0
    while True:
        try:
            async with get_semaphore():
                return await fn()
        except Exception as exc:
            if is_tool_call_failure(exc) and gen_failures < GENERATION_RETRIES:
                gen_failures += 1  # model glitch, not load: retry immediately
                continue
            if not is_rate_limited(exc) or attempt >= retries:
                raise
            delay = min(max_delay, base_delay * 2**attempt) * (0.5 + random.random() / 2)
            attempt += 1
            await asyncio.sleep(delay)


def make_chat_model(role: str):
    """Chat model for a role (planner, researcher, synthesis, writer, fact_check)."""
    from langchain_groq import ChatGroq

    s = get_settings()
    return ChatGroq(model=s.model_for(role), api_key=s.groq_api_key, temperature=0)
