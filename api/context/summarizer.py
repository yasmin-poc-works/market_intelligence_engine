"""LLM-backed summarizer used for budget compression (fit_to_budget) and session
summaries. Long agent conversations are additionally auto-summarized by the
SummarizationMiddleware that deepagents' create_deep_agent enables by default
(it offloads evicted history to the agent's backend)."""
from langchain_groq import ChatGroq

from config import get_settings
from context.token_budget import Summarizer


def make_llm_summarizer(role: str = "synthesis") -> Summarizer:
    s = get_settings()
    model = ChatGroq(model=s.model_for(role), api_key=s.groq_api_key, temperature=0)

    def summarize(text: str, target_tokens: int) -> str:
        prompt = (
            f"Summarize the following in at most {target_tokens} tokens. Keep names, figures, "
            f"dates and source attributions; drop filler. Output only the summary.\n\n{text}"
        )
        return str(model.invoke(prompt).content).strip()

    return summarize
