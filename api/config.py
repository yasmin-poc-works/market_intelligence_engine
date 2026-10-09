from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from qdrant_client import QdrantClient

REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", extra="ignore", env_ignore_empty=True
    )

    # LLM
    llm_provider: str = "groq"
    groq_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"
    planner_model: str | None = None
    researcher_model: str | None = None
    synthesis_model: str | None = None
    writer_model: str | None = None
    fact_check_model: str | None = None
    llm_max_concurrency: int = Field(3, ge=1)
    llm_max_retries: int = Field(5, ge=0)

    # Embeddings (fact-check similarity, Qdrant chunks)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Search
    search_provider: str = "serpapi"
    serpapi_api_key: str = ""

    # Qdrant
    qdrant_path: str = "data/qdrant"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None

    # Storage
    database_url: str = "sqlite:///data/insightforge.db"
    scratch_path: str = "data/scratch"

    # Pipeline
    evidence_token_budget: int = Field(6000, gt=0)
    max_parallel_subtasks: int = Field(6, ge=1, le=6)

    # Web
    frontend_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000"  # comma-separated
    upload_path: str = "data/uploads"

    # Auth
    jwt_secret: str = "b9bXOmdO5JnJtKI5Af1d_Wb3Z9w3ooCiI3SCNRkRydzy0la-RrRtbv38_rihxeC-"

    # Observability
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "insightforge"

    @field_validator("database_url")
    @classmethod
    def _absolute_sqlite(cls, v: str) -> str:
        # Anchor relative sqlite paths to the repo root so the cwd does not matter
        prefix = "sqlite:///"
        if v.startswith(prefix) and not v.startswith("sqlite:////") and ":memory:" not in v:
            return prefix + _resolve(v[len(prefix):]).as_posix()
        return v

    def model_for(self, role: str) -> str:
        """Model name for a role: planner, researcher, synthesis, writer, fact_check."""
        return getattr(self, f"{role}_model", None) or self.llm_model

    @property
    def qdrant_dir(self) -> Path:
        return _resolve(self.qdrant_path)

    @property
    def upload_dir(self) -> Path:
        return _resolve(self.upload_path)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def scratch_dir(self) -> Path:
        return _resolve(self.scratch_path)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_qdrant_client() -> QdrantClient:
    """Remote client if QDRANT_URL is set, else embedded local storage at QDRANT_PATH."""
    s = get_settings()
    if s.qdrant_url:
        return QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key)
    s.qdrant_dir.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(s.qdrant_dir))
