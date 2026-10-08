from typing import Any

from schemas import ResearchBrief


class FakeAgent:
    """Stands in for a deep agent: records prompts, replays canned structured responses
    (the last one repeats)."""

    def __init__(self, *responses: Any):
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def ainvoke(self, payload: dict) -> dict:
        self.prompts.append(payload["messages"][0][1])
        idx = min(len(self.prompts) - 1, len(self.responses) - 1)
        return {"structured_response": self.responses[idx]}


def brief(text: str, **kw) -> ResearchBrief:
    return ResearchBrief(text=text, **kw)

