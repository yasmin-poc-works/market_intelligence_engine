"""Production wiring of the research pipeline (real LLM stages)."""
from agents.fact_checker import make_fact_check_stage
from agents.synthesis import make_llm_extractor, make_synthesis_stage
from agents.writer import make_llm_generator, make_write_stage
from context.summarizer import make_llm_summarizer
from graph.events import EventRecorder
from graph.pipeline import PipelineDeps


def build_deps(recorder: EventRecorder) -> PipelineDeps:
    # NOTE: researcher tools are filled in Phase 2 (agents.researchers.RESEARCHER_TOOLS);
    # until then real runs have no sources to synthesize.
    return PipelineDeps(
        recorder=recorder,
        synthesize=make_synthesis_stage(make_llm_extractor(), make_llm_summarizer(), recorder=recorder),
        write=make_write_stage(make_llm_generator()),
        fact_check=make_fact_check_stage(),
    )
