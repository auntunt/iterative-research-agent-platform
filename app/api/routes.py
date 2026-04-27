from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.models.schemas import (
    AgentOutput,
    ContextStats,
    CriticAssessment,
    EvidenceCard,
    EvidencePage,
    IngestionResultSchema,
    KnowledgeIngestRequest,
    KnowledgeIngestResponse,
    KnowledgeSearchResult,
    LogPage,
    MetricsResponse,
    ResumeRequest,
    RoutingDecisionSchema,
    RunTaskRequest,
    TaskListResponse,
    TaskState,
    TaskStatus,
)
from app.services.queue import QueueFullError
from app.services.artifacts import ResearchArtifactExporter
from app.services.search_health import SearchHealthChecker
from app.services.task_manager import TaskNotFoundError

router = APIRouter()


@router.post("/run_task", response_model=TaskState, status_code=status.HTTP_202_ACCEPTED)
async def run_task(payload: RunTaskRequest, request: Request) -> TaskState:
    task_manager = request.app.state.task_manager
    task_queue = request.app.state.task_queue

    task = await task_manager.create_task(
        payload.task,
        payload.metadata,
        request_id=getattr(request.state, "request_id", ""),
    )
    await task_manager.set_status(task.task_id, TaskStatus.RUNNING)
    try:
        await task_queue.enqueue(task.task_id)
    except QueueFullError as exc:
        await task_manager.set_status(task.task_id, TaskStatus.FAILED, error=str(exc))
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    return await task_manager.get_task(task.task_id)


@router.get("/task/{task_id}", response_model=TaskState)
async def get_task(task_id: str, request: Request) -> TaskState:
    try:
        return await request.app.state.task_manager.get_task(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found") from exc


@router.delete("/task/{task_id}", status_code=status.HTTP_200_OK)
async def delete_task(task_id: str, request: Request) -> dict[str, object]:
    deleted = await request.app.state.task_manager.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return {"task_id": task_id, "deleted": True}


@router.get("/task/{task_id}/evidence", response_model=EvidencePage)
async def get_task_evidence(task_id: str, request: Request) -> EvidencePage:
    try:
        task = await request.app.state.task_manager.get_task(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found") from exc

    cards_by_id: dict[str, EvidenceCard] = {}
    assessments: list[CriticAssessment] = []
    for step in task.steps:
        if not step.output:
            continue
        try:
            output = AgentOutput.model_validate_json(step.output)
        except Exception:
            continue
        for raw_card in output.metadata.get("evidence_cards", []):
            card = EvidenceCard.model_validate(raw_card)
            cards_by_id[card.card_id] = card
        if step.agent == "critic":
            assessments.append(CriticAssessment.model_validate(output.metadata))

    return EvidencePage(
        task_id=task_id,
        cards=list(cards_by_id.values()),
        critic_assessments=assessments,
    )


@router.post("/task/{task_id}/artifacts")
async def export_task_artifacts(task_id: str, request: Request) -> dict[str, object]:
    try:
        task = await request.app.state.task_manager.get_task(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found") from exc
    logs = await request.app.state.task_manager.get_logs(task_id=task_id, limit=500)
    exporter = ResearchArtifactExporter()
    return exporter.export(task, logs)


@router.get("/search/providers")
async def search_providers(request: Request) -> dict[str, object]:
    checker = SearchHealthChecker(request.app.state.settings)
    return await checker.check()


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
) -> TaskListResponse:
    task_manager = request.app.state.task_manager
    offset = (page - 1) * page_size
    tasks = await task_manager.list_tasks(limit=page_size, offset=offset)
    metrics = await task_manager.platform_metrics()
    total = await task_manager.task_count()
    total_pages = (total + page_size - 1) // page_size if total else 0
    return TaskListResponse(
        tasks=tasks,
        metrics=metrics,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


@router.get("/metrics", response_model=MetricsResponse)
async def metrics(request: Request) -> MetricsResponse:
    task_manager = request.app.state.task_manager
    task_metrics = await task_manager.platform_metrics()
    status_counts = await task_manager.status_counts()
    task_count = await task_manager.task_count()
    queue_stats = await request.app.state.task_queue.stats()
    return MetricsResponse(
        task_metrics=task_metrics,
        task_count=task_count,
        status_counts=status_counts,
        provider_usage=task_metrics.provider_usage,
        queue=queue_stats,
    )


@router.get("/logs", response_model=LogPage)
async def logs(
    request: Request,
    task_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> LogPage:
    entries = await request.app.state.task_manager.get_logs(task_id=task_id, limit=limit, offset=offset)
    return LogPage(logs=entries, limit=limit, offset=offset)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ------------------------------------------------------------------
# 新增端点：自适应路由日志
# ------------------------------------------------------------------

@router.get("/task/{task_id}/routing_log", response_model=list[RoutingDecisionSchema])
async def get_routing_log(task_id: str, request: Request) -> list[RoutingDecisionSchema]:
    """返回该任务所有 LLM 调用的模型选择决策，供前端可视化。"""
    try:
        task = await request.app.state.task_manager.get_task(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found") from exc

    decisions: list[RoutingDecisionSchema] = []
    for step in task.steps:
        if not step.output:
            continue
        try:
            output = AgentOutput.model_validate_json(step.output)
        except Exception:
            continue
        for rd in output.metadata.get("routing_decisions", []):
            decisions.append(RoutingDecisionSchema.model_validate(rd))
    return decisions


# ------------------------------------------------------------------
# 新增端点：上下文管理统计
# ------------------------------------------------------------------

@router.get("/task/{task_id}/context_stats", response_model=ContextStats)
async def get_context_stats(task_id: str, request: Request) -> ContextStats:
    """返回该任务的上下文压缩统计和滚动摘要。"""
    memory = request.app.state.memory
    rolling_summary = await memory.get_rolling_summary(task_id)
    compression_stats = await memory.get_compression_stats(task_id)
    return ContextStats(
        rolling_summary=rolling_summary,
        compression_count=compression_stats.get("compression_count", 0),
        tokens_saved=compression_stats.get("tokens_saved", 0),
    )


# ------------------------------------------------------------------
# 新增端点：Human-in-the-loop 恢复
# ------------------------------------------------------------------

@router.post("/task/{task_id}/resume", response_model=TaskState)
async def resume_task(task_id: str, body: ResumeRequest, request: Request) -> TaskState:
    """从 HIL 暂停点恢复任务。"""
    orchestrator = request.app.state.orchestrator
    approved = None if body.action == "reject_all" else body.approved_cards
    try:
        return await orchestrator.resume_task(task_id, approved_cards=approved)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ------------------------------------------------------------------
# 新增端点：RAG 知识库
# ------------------------------------------------------------------

@router.get("/rag/stats")
async def rag_stats(request: Request) -> dict[str, object]:
    """返回知识库统计信息。"""
    orchestrator = request.app.state.orchestrator
    if not orchestrator.ingester:
        return {"enabled": False, "message": "RAG 未启用（rag_enabled=false 或初始化失败）"}
    stats = await orchestrator.ingester.vector_store.stats()
    history = orchestrator.ingester.ingestion_history()
    knowledge_history = orchestrator.ingester.knowledge_ingestion_history()
    return {"enabled": True, **stats, "ingestion_history": history[-10:], "knowledge_ingestion_history": knowledge_history[-10:]}


@router.post("/rag/knowledge", response_model=KnowledgeIngestResponse)
async def ingest_knowledge(payload: KnowledgeIngestRequest, request: Request) -> KnowledgeIngestResponse:
    """将外部知识资料写入 RAG 知识库。"""
    orchestrator = request.app.state.orchestrator
    if not orchestrator.ingester:
        raise HTTPException(status_code=503, detail="RAG 未启用")
    result = await orchestrator.ingester.ingest_knowledge(
        source_id=payload.source_id,
        title=payload.title,
        text=payload.text,
        source_url=payload.source_url,
        source_type=payload.source_type,
        metadata=payload.metadata,
    )
    return KnowledgeIngestResponse(
        source_id=result.source_id,
        title=result.title,
        chunks_written=result.chunks_written,
        chunks_skipped=result.chunks_skipped,
        duration_ms=result.duration_ms,
        error=result.error,
    )


@router.get("/rag/knowledge/search", response_model=list[KnowledgeSearchResult])
async def search_knowledge(
    request: Request,
    q: str = Query(..., min_length=1, description="查询文本"),
    top_k: int = Query(default=5, ge=1, le=20),
) -> list[KnowledgeSearchResult]:
    """只检索主动写入的知识库资料。"""
    orchestrator = request.app.state.orchestrator
    if not orchestrator.ingester:
        raise HTTPException(status_code=503, detail="RAG 未启用")
    results = await orchestrator.ingester.search_knowledge(q, top_k=top_k)
    return [
        KnowledgeSearchResult(doc_id=r.doc_id, text=r.text, score=r.score, metadata=r.metadata)
        for r in results
    ]


@router.get("/rag/search")
async def rag_search(
    q: str = Query(..., min_length=1, description="查询文本"),
    top_k: int = Query(default=5, ge=1, le=20),
    request: Request = None,
) -> list[dict[str, object]]:
    """手动查询向量知识库（演示用）。"""
    orchestrator = request.app.state.orchestrator
    if not orchestrator.ingester:
        raise HTTPException(status_code=503, detail="RAG 未启用")
    results = await orchestrator.ingester.search(q, top_k=top_k)
    return [
        {"doc_id": r.doc_id, "text": r.text, "score": r.score, "metadata": r.metadata}
        for r in results
    ]


@router.delete("/rag/task/{task_id}")
async def delete_rag_task(task_id: str, request: Request) -> dict[str, object]:
    """删除某任务在知识库中的所有记录。"""
    orchestrator = request.app.state.orchestrator
    if not orchestrator.ingester:
        raise HTTPException(status_code=503, detail="RAG 未启用")
    deleted = await orchestrator.ingester.vector_store.delete_by_task(task_id)
    return {"task_id": task_id, "deleted_chunks": deleted}


@router.delete("/rag/knowledge/{source_id}")
async def delete_knowledge_source(source_id: str, request: Request) -> dict[str, object]:
    """删除某个外部知识源在向量库中的所有 chunk。"""
    orchestrator = request.app.state.orchestrator
    if not orchestrator.ingester:
        raise HTTPException(status_code=503, detail="RAG 未启用")
    deleted = await orchestrator.ingester.delete_knowledge_source(source_id)
    return {"source_id": source_id, "deleted_chunks": deleted}
