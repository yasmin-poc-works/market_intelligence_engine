"""Pydantic models for every agent boundary. Agents exchange references
(source_id, chunk_id), never raw content, except Synthesis."""
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RunStatus(StrEnum):
    PLANNING = "PLANNING"
    RESEARCHING = "RESEARCHING"
    SYNTHESIZING = "SYNTHESIZING"
    WRITING = "WRITING"
    FACT_CHECKING = "FACT_CHECKING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


class ResearchBrief(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    document_ids: list[str] = Field(default_factory=list, max_length=10)
    include_web: bool = True
    include_documents: bool = True
    clarification: str | None = None


class SubTask(BaseModel):
    id: str
    title: str
    scope: str
    researcher: Literal["web", "document", "entity"] = "web"
    status: Literal["pending", "running", "done", "failed"] = "pending"
    error: str | None = None


class TaskGraph(BaseModel):
    brief: str
    subtasks: list[SubTask] = Field(min_length=3, max_length=6)


class SourceRecord(BaseModel):
    """Stored in the scratch store; never passed between agents in full."""

    source_id: str
    url: str
    title: str
    published_date: datetime | None = None
    body_text: str
    credibility_score: float = Field(ge=0, le=1)
    byline: str | None = None


class SourceRef(BaseModel):
    """What a researcher returns instead of content."""

    source_id: str
    summary: str
    kind: Literal["web", "document_chunk", "entity"] = "web"
    url: str | None = None


class Entity(BaseModel):
    id: str
    name: str
    type: Literal["company", "person", "product", "funding", "figure", "date", "region"]
    attributes: dict[str, str] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)
    conflict: bool = False


class Relationship(BaseModel):
    source: str
    target: str
    relation: str
    source_ids: list[str] = Field(default_factory=list)


class EntityGraph(BaseModel):
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    claim: str
    source_ids: list[str] = Field(min_length=1)
    confidence: Confidence
    published_date: datetime | None = None
    credibility_score: float = Field(ge=0, le=1)
    conflict: bool = False


class EvidenceBundle(BaseModel):
    subtask_id: str | None = None
    items: list[EvidenceItem]
    token_count: int = 0
    compressed: bool = False


class ReportSection(BaseModel):
    heading: str
    body: str
    confidence: Confidence


class Report(BaseModel):
    id: str
    run_id: str
    title: str
    executive_summary: str
    sections: list[ReportSection]
    gaps_and_limitations: str = ""
    markdown: str = ""
    created_at: datetime


class FactCheckSummary(BaseModel):
    total_checked: int
    passed: int
    failed: int
    pass_rate: float = Field(ge=0, le=1)
    unverified_claims: list[str] = Field(default_factory=list)


class DiffSummary(BaseModel):
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    material: bool = False


class RunState(BaseModel):
    """Typed LangGraph state passed across node transitions."""

    run_id: str
    brief: ResearchBrief
    status: RunStatus = RunStatus.PLANNING
    task_graph: TaskGraph | None = None
    refs: list[SourceRef] = Field(default_factory=list)
    entity_graph: EntityGraph | None = None
    evidence: EvidenceBundle | None = None
    report: Report | None = None
    fact_check: FactCheckSummary | None = None
    error: str | None = None
