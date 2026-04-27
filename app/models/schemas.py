from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    SUCCESS = "success"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    SUCCESS = "success"


class ErrorCategory(str, Enum):
    LLM_ERROR = "llm_error"
    TOOL_ERROR = "tool_error"
    AGENT_ERROR = "agent_error"
    SYSTEM_ERROR = "system_error"
    VALIDATION_ERROR = "validation_error"
    ORCHESTRATION_ERROR = "orchestration_error"
    UNKNOWN = "unknown"

    @classmethod
    def legacy(cls, value: str) -> "ErrorCategory":
        aliases = {
            "llm_provider": cls.LLM_ERROR,
            "tool": cls.TOOL_ERROR,
            "validation": cls.VALIDATION_ERROR,
            "orchestration": cls.ORCHESTRATION_ERROR,
        }
        return aliases.get(value, cls(value))


class RunTaskRequest(BaseModel):
    task: str = Field(..., min_length=1, description="Natural-language task request")
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMUsage(BaseModel):
    provider: str
    model: str
    latency: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    fallback_used: bool = False


class ToolResult(BaseModel):
    tool: str
    success: bool
    output: str
    latency: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    thought: str
    action: str
    result: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    url: str
    title: str
    paragraph_id: str
    snippet: str


class EvidenceCard(BaseModel):
    card_id: str
    topic_id: str
    claim: str
    summary: str
    citation: Citation
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    source_type: str = "web"
    created_at: datetime = Field(default_factory=utc_now)


class ResearchTopic(BaseModel):
    topic_id: str
    question: str
    search_queries: list[str] = Field(default_factory=list)
    rationale: str = ""


class CriticAssessment(BaseModel):
    sufficient: bool = False
    coverage_score: float = Field(default=0.0, ge=0.0, le=1.0)
    faithfulness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    answer_relevancy_score: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_topics: list[str] = Field(default_factory=list)
    follow_up_topics: list[ResearchTopic] = Field(default_factory=list)
    notes: str = ""


class StepState(BaseModel):
    step_id: int
    agent: str
    input: str
    output: str = ""
    status: StepStatus = StepStatus.PENDING
    llm_provider: str = ""
    llm_model: str = ""
    latency: float = 0.0
    retries: int = 0
    error: str | None = None
    error_category: ErrorCategory | None = None
    tool_calls: list[ToolResult] = Field(default_factory=list)
    llm_usage: list[LLMUsage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    trace_id: str = ""
    depends_on: list[int] = Field(default_factory=list)


class TaskMetrics(BaseModel):
    total_steps: int = 0
    successful_steps: int = 0
    failed_steps: int = 0
    retries: int = 0
    latency: float = 0.0
    success_rate: float = 0.0
    llm_calls: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    avg_latency: float = 0.0
    provider_usage: dict[str, int] = Field(default_factory=dict)


class TaskGraphStep(BaseModel):
    step_id: int
    agent: str
    goal: str
    depends_on: list[int] = Field(default_factory=list)
    enabled: bool = True


class TaskGraph(BaseModel):
    complexity: str = "simple"
    steps: list[TaskGraphStep] = Field(default_factory=list)
    replan_reason: str | None = None


class TaskState(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: str = ""
    task: str
    status: TaskStatus = TaskStatus.PENDING
    steps: list[StepState] = Field(default_factory=list)
    result: str = ""
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    metrics: TaskMetrics = Field(default_factory=TaskMetrics)
    task_graph: TaskGraph | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TaskListResponse(BaseModel):
    tasks: list[TaskState]
    metrics: TaskMetrics
    page: int = 1
    page_size: int = 10
    total: int = 0
    total_pages: int = 0


class EvidencePage(BaseModel):
    task_id: str
    cards: list[EvidenceCard]
    critic_assessments: list[CriticAssessment] = Field(default_factory=list)


class LogEntry(BaseModel):
    id: int | None = None
    task_id: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    level: str = "INFO"
    event: str
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class LogPage(BaseModel):
    logs: list[LogEntry]
    limit: int
    offset: int


class MetricsResponse(BaseModel):
    task_metrics: TaskMetrics
    task_count: int
    status_counts: dict[str, int]
    provider_usage: dict[str, int]
    queue: dict[str, Any]


class RoutingDecisionSchema(BaseModel):
    agent: str
    provider: str
    model: str
    tier: str                    # nano / medium / large
    complexity_score: float
    signals: dict[str, float] = Field(default_factory=dict)
    reason: str = ""
    estimated_cost_usd: float = 0.0
    fallback_used: bool = False


class ContextStats(BaseModel):
    compression_count: int = 0
    tokens_saved: int = 0
    rolling_summary: str = ""


class IngestionResultSchema(BaseModel):
    task_id: str
    card_count: int
    chunks_written: int
    chunks_skipped: int
    duration_ms: float
    error: str | None = None


class KnowledgeIngestRequest(BaseModel):
    source_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    source_url: str = ""
    source_type: str = "manual"
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeIngestResponse(BaseModel):
    source_id: str
    title: str
    chunks_written: int
    chunks_skipped: int
    duration_ms: float
    error: str | None = None


class KnowledgeSearchResult(BaseModel):
    doc_id: str
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResumeRequest(BaseModel):
    approved_cards: list[str] = Field(default_factory=list)
    action: str = "approve"      # approve | reject_all
