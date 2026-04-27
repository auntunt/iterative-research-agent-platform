from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from app.agents import AgentRegistry, CriticAgent, PlannerAgent, ResearchAgent, WritingAgent
from app.agents.base_agent import AgentRun, BaseAgent
from app.context.summarizer import IncrementalSummarizer
from app.core.config import Settings
from app.core.llm_router import LLMRouter
from app.core.logger import bind_trace, get_logger, log_context
from app.models.schemas import (
    CriticAssessment,
    ErrorCategory,
    EvidenceCard,
    LogEntry,
    ResearchTopic,
    TaskGraph,
    TaskGraphStep,
    TaskState,
    TaskStatus,
)
from app.services.memory import MemoryStore
from app.services.task_manager import TaskManager
from app.tools import DocumentReaderTool, MarkdownRenderTool, WebFetchTool, WebSearchTool

logger = get_logger(__name__)


class ResearchGraphState(TypedDict, total=False):
    task_id: str
    task_state: TaskState
    task: str
    context: dict[str, Any]
    topics: list[ResearchTopic]
    pending_topics: list[ResearchTopic]
    evidence_cards: list[EvidenceCard]
    assessment: CriticAssessment
    round_index: int
    max_research_rounds: int
    final_output: str
    rag_cache_hits: int           # 知识库命中次数
    routing_decisions: list[dict] # 所有模型选择决策（序列化后存储）
    human_approved: bool          # HIL 审核结果


class Orchestrator:
    def __init__(self, settings: Settings, task_manager: TaskManager, memory: MemoryStore) -> None:
        self.settings = settings
        self.task_manager = task_manager
        self.memory = memory
        self.llm_router = LLMRouter(settings)
        self.summarizer = IncrementalSummarizer(self.llm_router)
        self.agents: dict[str, BaseAgent] = {
            "planner": PlannerAgent(self.llm_router),
            "researcher": ResearchAgent(
                self.llm_router,
                tools=[WebSearchTool(settings), WebFetchTool(settings), DocumentReaderTool(settings)],
            ),
            "critic": CriticAgent(self.llm_router),
            "writer": WritingAgent(self.llm_router, tools=[MarkdownRenderTool()]),
        }
        self.registry = AgentRegistry(settings, self.agents)

        # RAG 组件（可选）
        self.ingester: Any = None
        if settings.rag_enabled:
            self._init_rag(settings)

        # LangGraph checkpoint（SQLite 持久化，支持断点续跑）
        self.checkpointer = self._init_checkpointer(settings)
        self.graph = self._build_graph()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def _init_rag(self, settings: Settings) -> None:
        try:
            from app.rag.auto_ingester import AutoIngester
            from app.rag.embedder import Embedder
            from app.rag.semantic_chunker import SemanticChunker
            from app.rag.vector_store import VectorStore

            embedder = Embedder(settings)
            vector_store = VectorStore(settings)
            chunker = SemanticChunker(
                embedder,
                threshold=settings.rag_chunk_threshold,
                max_chunk_tokens=settings.rag_chunk_max_tokens,
            )
            self.ingester = AutoIngester(vector_store, embedder, chunker, settings)
            logger.info("rag_initialized", extra=log_context(persist_dir=settings.rag_chroma_persist_dir))
        except Exception as exc:
            logger.warning("rag_init_failed", extra=log_context(error=str(exc)))
            self.ingester = None

    def _init_checkpointer(self, settings: Settings) -> Any:
        """
        初始化 LangGraph checkpointer，支持 HIL 断点续跑。

        策略：先创建内存 checkpointer 保证图立即可用；首次 execute_task 时
        通过 _maybe_upgrade_checkpointer() 异步升级为 SQLite 持久化版本。
        """
        try:
            from langgraph.checkpoint.memory import MemorySaver
            self._checkpoint_db = settings.langgraph_checkpoint_db
            return MemorySaver()
        except Exception as exc:
            logger.warning("checkpoint_init_failed", extra=log_context(error=str(exc)))
            return None

    async def _maybe_upgrade_checkpointer(self) -> None:
        """首次调用时尝试升级到 AsyncSqliteSaver（持久化跨进程 HIL 状态）。"""
        from langgraph.checkpoint.memory import MemorySaver
        if not isinstance(self.checkpointer, MemorySaver):
            return
        try:
            import pathlib
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            pathlib.Path(self._checkpoint_db).parent.mkdir(parents=True, exist_ok=True)
            self._checkpoint_conn = await aiosqlite.connect(self._checkpoint_db)
            self.checkpointer = AsyncSqliteSaver(self._checkpoint_conn)
            self.graph = self._build_graph()
            logger.info("checkpoint_upgraded_to_sqlite", extra=log_context(db=self._checkpoint_db))
        except Exception as exc:
            logger.info("checkpoint_stays_in_memory", extra=log_context(reason=str(exc)))

    async def close(self) -> None:
        """关闭持久化资源（由 lifespan 调用）。"""
        conn = getattr(self, "_checkpoint_conn", None)
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # LangGraph 图构建
    # ------------------------------------------------------------------

    def _build_graph(self):
        """
        四节点外层图：plan → research → human_review → write

        设计亮点：
        - research 节点内部用 asyncio.gather 并行执行所有 researcher（等价 Send API 语义）
        - human_review 节点在 interrupt_before 处暂停，等待 POST /task/{id}/resume
        - checkpoint 持久化每个节点完成后的完整状态，支持中断恢复
        - 外层路由：Critic 评分低 → human_review；高 → 直接 write
        """
        graph = StateGraph(ResearchGraphState)
        graph.add_node("plan",         self._graph_plan)
        graph.add_node("research",     self._graph_research)
        graph.add_node("critic",       self._graph_critic)
        graph.add_node("human_review", self._graph_human_review)
        graph.add_node("write",        self._graph_write)

        graph.set_entry_point("plan")
        graph.add_edge("plan", "research")
        graph.add_edge("research", "critic")
        graph.add_conditional_edges(
            "critic",
            self._route_after_critic,
            {
                "research":      "research",    # 证据不足，继续检索
                "human_review":  "human_review", # 质量低，触发人工审核
                "write":         "write",        # 充分，直接写报告
            },
        )
        graph.add_edge("human_review", "write")
        graph.add_edge("write", END)

        compile_kwargs: dict[str, Any] = {}
        if self.checkpointer:
            compile_kwargs["checkpointer"] = self.checkpointer
            compile_kwargs["interrupt_before"] = ["human_review"]

        return graph.compile(**compile_kwargs)

    # ------------------------------------------------------------------
    # 图节点
    # ------------------------------------------------------------------

    async def _graph_plan(self, state: ResearchGraphState) -> ResearchGraphState:
        task_id = state["task_id"]
        task_state = state["task_state"]
        context = dict(state["context"])
        plan_run = await self._plan(task_id, state["task"], context)
        topics = [
            ResearchTopic.model_validate(t)
            for t in plan_run.output.metadata.get("research_topics", [])
        ]
        context["research_topics"] = [t.model_dump(mode="json") for t in topics]

        routing_decisions = list(state.get("routing_decisions") or [])
        routing_decisions.extend(self._serialize_routing(plan_run))

        await self._record_event(
            task_id, task_state.trace_id, task_state.request_id,
            "langgraph_node_completed",
            {"node": "plan", "topics": len(topics)},
        )
        return {
            **state,
            "context": context,
            "topics": topics,
            "pending_topics": topics,
            "routing_decisions": routing_decisions,
        }

    async def _graph_research(self, state: ResearchGraphState) -> ResearchGraphState:
        """
        并行研究节点：对所有 pending_topics 同时触发 asyncio.gather，
        实现与 LangGraph Send API 相同的并行语义，且状态合并无冲突。
        """
        task_id = state["task_id"]
        task_state = state["task_state"]
        context = dict(state["context"])
        evidence_cards = list(state.get("evidence_cards", []))
        round_index = int(state.get("round_index", 0))
        pending_topics = list(state.get("pending_topics", []))
        rag_cache_hits = int(state.get("rag_cache_hits") or 0)
        routing_decisions = list(state.get("routing_decisions") or [])

        if not pending_topics:
            return state

        # RAG 预查询：命中则直接生成证据卡片，不做网络搜索
        rag_topics, web_topics = await self._split_topics_by_cache(pending_topics, rag_cache_hits)
        rag_cache_hits += len(rag_topics)

        for topic, cached_cards in rag_topics:
            seen = {c.card_id for c in evidence_cards}
            for card in cached_cards:
                if card.card_id not in seen:
                    evidence_cards.append(card)
                    seen.add(card.card_id)

        # 并行执行网络检索（等价 Send API 的并行效果）
        if web_topics:
            runs = await self._run_research_parallel(task_id, task_state, web_topics, context, round_index)
            seen = {c.card_id for c in evidence_cards}
            for run in runs:
                routing_decisions.extend(self._serialize_routing(run))
                for raw_card in run.output.metadata.get("evidence_cards", []):
                    card = EvidenceCard.model_validate(raw_card)
                    if card.card_id not in seen:
                        seen.add(card.card_id)
                        evidence_cards.append(card)
                        await self.memory.append(task_id, {
                            "event": "evidence_card_created",
                            "card": card.model_dump(mode="json"),
                        })

        context["evidence_cards"] = [c.model_dump(mode="json") for c in evidence_cards]
        context["evidence_window"] = self._evidence_window(evidence_cards)
        await self._record_event(
            task_id, task_state.trace_id, task_state.request_id,
            "langgraph_node_completed",
            {
                "node": "research", "round_index": round_index,
                "pending_topics": len(pending_topics), "evidence_cards": len(evidence_cards),
                "rag_cache_hits": rag_cache_hits,
            },
        )
        return {
            **state,
            "context": context,
            "evidence_cards": evidence_cards,
            "rag_cache_hits": rag_cache_hits,
            "routing_decisions": routing_decisions,
        }

    async def _graph_critic(self, state: ResearchGraphState) -> ResearchGraphState:
        task_id = state["task_id"]
        task_state = state["task_state"]
        context = dict(state["context"])
        round_index = int(state.get("round_index", 0))
        routing_decisions = list(state.get("routing_decisions") or [])

        evidence_cards = state.get("evidence_cards", [])
        context["evidence_cards"] = [c.model_dump(mode="json") for c in evidence_cards]
        context["evidence_window"] = self._evidence_window(evidence_cards)

        critic_run = await self._run_agent_step(
            task_id, self.agents["critic"], task_state.task,
            {**context, "round_index": round_index},
            TaskGraphStep(step_id=0, agent="critic", goal=f"评估第 {round_index + 1} 轮研究后的证据充分性", depends_on=[]),
        )
        assessment = CriticAssessment.model_validate(critic_run.output.metadata)
        context["critic_assessment"] = assessment.model_dump(mode="json")
        routing_decisions.extend(self._serialize_routing(critic_run))

        await self._record_event(
            task_id, task_state.trace_id, task_state.request_id,
            "critic_assessment_completed",
            assessment.model_dump(mode="json"),
            level="INFO" if assessment.sufficient else "WARN",
        )
        await self._record_event(
            task_id, task_state.trace_id, task_state.request_id,
            "langgraph_node_completed",
            {
                "node": "critic", "round_index": round_index,
                "sufficient": assessment.sufficient,
                "coverage_score": assessment.coverage_score,
                "follow_up_topics": len(assessment.follow_up_topics),
            },
        )
        return {
            **state,
            "context": context,
            "assessment": assessment,
            "pending_topics": assessment.follow_up_topics,
            "round_index": round_index + 1,
            "routing_decisions": routing_decisions,
        }

    async def _graph_human_review(self, state: ResearchGraphState) -> ResearchGraphState:
        """
        Human-in-the-loop 节点。

        图在 interrupt_before=["human_review"] 处暂停，
        前端调用 POST /task/{id}/resume 后通过 aupdate_state 写入审核意见，
        图从此节点继续执行。
        """
        task_id = state["task_id"]
        task_state = state["task_state"]
        approved = state.get("human_approved", False)
        threshold = self.settings.hil_min_confidence_threshold

        if not approved:
            original_count = len(state.get("evidence_cards", []))
            filtered = [c for c in state.get("evidence_cards", []) if c.confidence >= threshold]
            await self._record_event(
                task_id, task_state.trace_id, task_state.request_id,
                "human_review_filtered",
                {"original": original_count, "kept": len(filtered), "threshold": threshold},
                level="WARN",
            )
            return {**state, "evidence_cards": filtered}

        await self._record_event(
            task_id, task_state.trace_id, task_state.request_id,
            "human_review_approved", {},
        )
        return state

    async def _graph_write(self, state: ResearchGraphState) -> ResearchGraphState:
        task_id = state["task_id"]
        task_state = state["task_state"]
        context = dict(state["context"])
        routing_decisions = list(state.get("routing_decisions") or [])

        final_run = await self._run_agent_step(
            task_id, self.agents["writer"], task_state.task, context,
            TaskGraphStep(step_id=0, agent="writer", goal="撰写带引用追溯的最终中文研究报告", depends_on=[]),
        )
        routing_decisions.extend(self._serialize_routing(final_run))

        await self._record_event(
            task_id, task_state.trace_id, task_state.request_id,
            "langgraph_node_completed",
            {"node": "write", "characters": len(final_run.output.result)},
        )

        # 异步 RAG 入库（不阻塞响应）
        evidence_cards = state.get("evidence_cards", [])
        if self.ingester and evidence_cards:
            asyncio.create_task(
                self._ingest_to_rag(task_id, task_state.trace_id, task_state.request_id, evidence_cards)
            )

        return {**state, "final_output": final_run.output.result, "routing_decisions": routing_decisions}

    # ------------------------------------------------------------------
    # 路由函数
    # ------------------------------------------------------------------

    @staticmethod
    def _route_after_critic(state: ResearchGraphState) -> Literal["research", "human_review", "write"]:
        """
        三路路由：
        - 证据充分 → write
        - 轮次耗尽 → human_review（让人决定是否发布）
        - 仍不足且未超轮次 → research（继续）
        """
        assessment = state.get("assessment", CriticAssessment())
        round_index = int(state.get("round_index", 0))
        max_rounds = int(state.get("max_research_rounds", 2))

        if assessment.sufficient:
            return "write"
        if round_index >= max_rounds or not state.get("pending_topics"):
            # 超过最大轮次：触发人工审核节点（如无 checkpointer 则直接进 write）
            return "human_review"
        return "research"

    # ------------------------------------------------------------------
    # 主执行入口
    # ------------------------------------------------------------------

    async def execute_task(self, task_id: str, resume: bool = False) -> TaskState:
        await self._maybe_upgrade_checkpointer()
        task_state = await self.task_manager.get_task(task_id)
        bind_trace(task_state.request_id, task_state.trace_id)
        await self.task_manager.set_status(task_id, TaskStatus.RUNNING)
        max_research_rounds = int(task_state.metadata.get("max_research_rounds", self.settings.max_research_rounds))
        min_evidence_per_topic = int(task_state.metadata.get("min_evidence_per_topic", self.settings.min_evidence_per_topic))
        context: dict[str, Any] = {
            **task_state.metadata,
            "enabled_agents": self.settings.enabled_agents,
            "trace_id": task_state.trace_id,
            "request_id": task_state.request_id,
            "max_research_rounds": max_research_rounds,
            "min_evidence_per_topic": min_evidence_per_topic,
            "max_sources_per_query": int(task_state.metadata.get("max_sources_per_query", self.settings.max_sources_per_query)),
        }

        invoke_config: dict[str, Any] = {}
        if self.checkpointer:
            invoke_config["config"] = {"configurable": {"thread_id": task_id}}

        try:
            if resume and self.checkpointer:
                graph_state = await self.graph.ainvoke(None, **invoke_config)
            else:
                graph_state = await self.graph.ainvoke(
                    ResearchGraphState(
                        task_id=task_id,
                        task_state=task_state,
                        task=task_state.task,
                        context=context,
                        topics=[],
                        pending_topics=[],
                        evidence_cards=[],
                        assessment=CriticAssessment(),
                        round_index=0,
                        max_research_rounds=max_research_rounds,
                        final_output="",
                        rag_cache_hits=0,
                        routing_decisions=[],
                        human_approved=False,
                    ),
                    **invoke_config,
                )

            final_output = graph_state["final_output"]
            evidence_cards = graph_state.get("evidence_cards", [])
            assessment = graph_state.get("assessment", CriticAssessment())
            routing_decisions = graph_state.get("routing_decisions", [])

            await self.task_manager.set_result(task_id, final_output)
            await self.memory.remember(
                f"task:{task_id}:summary",
                {
                    "task": task_state.task,
                    "trace_id": task_state.trace_id,
                    "evidence_cards": len(evidence_cards),
                    "critic_assessment": assessment.model_dump(mode="json"),
                    "routing_decisions_count": len(routing_decisions),
                    "rag_cache_hits": graph_state.get("rag_cache_hits", 0),
                    "result": final_output[:2000],
                },
            )
            return await self.task_manager.set_status(task_id, TaskStatus.SUCCESS)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("task_execution_failed", extra=log_context(task_id=task_id, trace_id=task_state.trace_id, error=str(exc)))
            await self.memory.append(task_id, {"event": "task_failed", "error": str(exc)})
            await self._record_event(
                task_id, task_state.trace_id, task_state.request_id,
                "task_failed",
                {"error": str(exc), "category": self.classify_error(exc).value},
                level="ERROR",
            )
            return await self.task_manager.set_status(task_id, TaskStatus.FAILED, error=str(exc))

    async def resume_task(self, task_id: str, approved_cards: list[str] | None = None) -> TaskState:
        """从 HIL 暂停点恢复任务。前端调用 POST /task/{id}/resume 时触发。"""
        if not self.checkpointer:
            raise RuntimeError("Checkpoint 未启用，无法恢复任务。")

        config = {"configurable": {"thread_id": task_id}}
        update: dict[str, Any] = {"human_approved": True}
        if approved_cards is not None:
            all_events = await self.memory.get(task_id)
            approved_set = set(approved_cards)
            filtered = [
                EvidenceCard.model_validate(e["card"])
                for e in all_events
                if e.get("event") == "evidence_card_created" and e["card"]["card_id"] in approved_set
            ]
            update["evidence_cards"] = filtered

        await self.graph.aupdate_state(config, update, as_node="human_review")
        asyncio.create_task(self.execute_task(task_id, resume=True))
        return await self.task_manager.get_task(task_id)

    # ------------------------------------------------------------------
    # 辅助：并行研究
    # ------------------------------------------------------------------

    async def _split_topics_by_cache(
        self, topics: list[ResearchTopic], current_hits: int
    ) -> tuple[list[tuple[ResearchTopic, list[EvidenceCard]]], list[ResearchTopic]]:
        """将 topics 按 RAG 命中情况分成两组：缓存命中组和需网络检索组。"""
        if not self.ingester:
            return [], topics

        rag_topics: list[tuple[ResearchTopic, list[EvidenceCard]]] = []
        web_topics: list[ResearchTopic] = []

        async def check(topic: ResearchTopic) -> tuple[ResearchTopic, list[EvidenceCard] | None]:
            cached = await self.ingester.check_cache(topic.question)
            if len(cached) >= self.settings.min_evidence_per_topic:
                cards = self._cached_results_to_cards(cached, topic)
                return topic, cards
            return topic, None

        results = await asyncio.gather(*(check(t) for t in topics))
        for topic, cards in results:
            if cards is not None:
                rag_topics.append((topic, cards))
            else:
                web_topics.append(topic)
        return rag_topics, web_topics

    async def _run_research_parallel(
        self,
        task_id: str,
        task_state: TaskState,
        topics: list[ResearchTopic],
        context: dict[str, Any],
        round_index: int,
    ) -> list[AgentRun]:
        """并行执行所有 topic 的 researcher（与 Send API 等价的并行语义）。"""
        if not topics:
            return []

        async def run_one(index: int, topic: ResearchTopic) -> AgentRun:
            return await self._run_agent_step(
                task_id, self.agents["researcher"], task_state.task,
                {**context, "round_index": round_index, "research_topic": topic.model_dump(mode="json")},
                TaskGraphStep(step_id=index, agent="researcher", goal=f"research {topic.topic_id}: {topic.question}", depends_on=[]),
            )

        runs = await asyncio.gather(*(run_one(i, t) for i, t in enumerate(topics, 1)))
        await self._record_event(
            task_id, task_state.trace_id, task_state.request_id,
            "research_round_completed",
            {
                "round_index": round_index,
                "topic_ids": [t.topic_id for t in topics],
                "evidence_cards": sum(len(r.output.metadata.get("evidence_cards", [])) for r in runs),
                "parallel_workers": len(topics),
            },
        )
        return list(runs)

    # ------------------------------------------------------------------
    # 辅助：任务步骤执行
    # ------------------------------------------------------------------

    async def _plan(self, task_id: str, task: str, context: dict[str, Any]) -> AgentRun:
        planner = await self._run_agent_step(task_id, self.agents["planner"], task, context)
        context["complexity"] = planner.metadata.get(
            "complexity", planner.output.metadata.get("complexity", "simple")
        )
        raw_graph = planner.output.metadata.get("task_graph") or planner.output.metadata.get("workflow")
        graph = TaskGraph.model_validate(raw_graph)
        await self.task_manager.set_task_graph(task_id, graph)
        context["plan"] = planner.output.result
        context["task_graph"] = graph.model_dump(mode="json")
        return planner

    async def _run_agent_step(
        self,
        task_id: str,
        agent: BaseAgent,
        task: str,
        context: dict[str, Any],
        graph_step: TaskGraphStep | None = None,
    ) -> AgentRun:
        step = await self.task_manager.add_step(
            task_id, agent.name,
            graph_step.goal if graph_step else task,
            depends_on=graph_step.depends_on if graph_step else [],
        )
        excluded_providers: set[str] = set(self.settings.provider_blacklist)
        last_error: Exception | None = None

        for attempt in range(self.settings.max_retries + 1):
            started = time.perf_counter()
            await self.task_manager.start_step(task_id, step.step_id, retries=attempt)
            try:
                run = await agent.run(task, {**context, "excluded_providers": excluded_providers})
                failed_tool = next((c for c in run.tool_calls if not c.success), None)
                if failed_tool:
                    raise RuntimeError(f"Tool failed: {failed_tool.tool}: {failed_tool.output}")

                latency = time.perf_counter() - started
                await self.task_manager.finish_step(
                    task_id, step.step_id, run.output.model_dump_json(),
                    latency, run.llm_usage, run.tool_calls,
                )
                await self.memory.append(task_id, {
                    "event": "agent_step_success",
                    "agent": agent.name,
                    "step_id": step.step_id,
                    "output": run.output.model_dump(mode="json"),
                })

                # 异步更新滚动摘要（不阻塞主流程）
                asyncio.create_task(self._update_rolling_summary(task_id, run))
                return run

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                category = self.classify_error(exc)
                if attempt < self.settings.max_retries:
                    logger.warning(
                        "agent_step_retry",
                        extra=log_context(
                            task_id=task_id, step_id=step.step_id, agent=agent.name,
                            attempt=attempt + 1, error=str(exc), category=category.value,
                        ),
                    )
                    await asyncio.sleep(self.settings.retry_backoff_seconds * (attempt + 1))
                    continue
                await self.task_manager.fail_step(task_id, step.step_id, str(exc), category, retries=attempt)

        raise RuntimeError(f"{agent.name} failed after retries: {last_error}") from last_error

    async def _record_event(
        self, task_id: str, trace_id: str, request_id: str,
        event: str, payload: dict[str, Any], level: str = "INFO",
    ) -> None:
        await self.memory.append(task_id, {"event": event, **payload})
        await self.task_manager.append_log(
            LogEntry(
                task_id=task_id, trace_id=trace_id, request_id=request_id,
                level=level, event=event, message=event, payload=payload,
            )
        )

    async def _update_rolling_summary(self, task_id: str, run: AgentRun) -> None:
        """agent 完成后异步更新滚动摘要，失败不影响主流程。"""
        try:
            agent_summary = await self.summarizer.summarize_agent_run(run.output.action, run)
            existing = await self.memory.get_rolling_summary(task_id)
            updated = await self.summarizer.rolling_update(existing, [{"event": "agent_completed", "summary": agent_summary}])
            await self.memory.set_rolling_summary(task_id, updated)
        except Exception as exc:
            logger.warning("rolling_summary_failed", extra=log_context(task_id=task_id, error=str(exc)))

    async def _ingest_to_rag(
        self, task_id: str, trace_id: str, request_id: str, evidence_cards: list[EvidenceCard]
    ) -> None:
        """异步将证据卡片入向量库。"""
        try:
            result = await self.ingester.ingest_task(task_id, evidence_cards)
            await self._record_event(
                task_id, trace_id, request_id,
                "rag_ingestion_completed",
                {"chunks_written": result.chunks_written, "chunks_skipped": result.chunks_skipped, "duration_ms": result.duration_ms},
            )
        except Exception as exc:
            logger.warning("rag_ingest_error", extra=log_context(task_id=task_id, error=str(exc)))

    # ------------------------------------------------------------------
    # 辅助：工具函数
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_routing(run: AgentRun) -> list[dict[str, Any]]:
        return [
            {
                "agent": rd.agent, "provider": rd.provider, "model": rd.model,
                "tier": rd.tier, "complexity_score": rd.complexity_score,
                "reason": rd.reason, "estimated_cost_usd": rd.estimated_cost_usd,
                "fallback_used": rd.fallback_used,
            }
            for rd in run.routing_decisions
        ]

    @staticmethod
    def _cached_results_to_cards(results: list[Any], topic: ResearchTopic) -> list[EvidenceCard]:
        from app.models.schemas import Citation
        import hashlib

        cards: list[EvidenceCard] = []
        for r in results:
            url = r.metadata.get("source_url", "")
            title = r.metadata.get("title") or r.metadata.get("source_url") or "知识库缓存"
            card_id = f"card-{hashlib.sha1(f'rag:{topic.topic_id}:{r.doc_id}'.encode()).hexdigest()[:12]}"
            cards.append(EvidenceCard(
                card_id=card_id,
                topic_id=topic.topic_id,
                claim=f"来自知识库的已验证证据：{r.text[:100]}",
                summary=r.text,
                citation=Citation(
                    url=url,
                    title=title,
                    paragraph_id=r.doc_id,
                    snippet=r.text[:240],
                ),
                confidence=min(1.0, r.score),
                source_type="knowledge_base",
            ))
        return cards

    def _evidence_window(self, cards: list[EvidenceCard]) -> list[dict[str, Any]]:
        return [c.model_dump(mode="json") for c in cards[-self.settings.evidence_window_size:]]

    @staticmethod
    def classify_error(error: Exception) -> ErrorCategory:
        import pydantic
        import httpx
        from app.core.exceptions import (
            LLMProviderError,
            OrchestratorError,
            QueueError,
            DatabaseError,
            SearchError,
            ToolError,
        )
        if isinstance(error, pydantic.ValidationError):
            return ErrorCategory.VALIDATION_ERROR
        if isinstance(error, (ToolError, SearchError)):
            return ErrorCategory.TOOL_ERROR
        if isinstance(error, LLMProviderError) or isinstance(error, httpx.HTTPError):
            return ErrorCategory.LLM_ERROR
        if isinstance(error, (OrchestratorError, QueueError, DatabaseError)):
            return ErrorCategory.SYSTEM_ERROR
        # asyncio.TimeoutError 通常来自 LLM 调用超时
        if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
            return ErrorCategory.LLM_ERROR
        return ErrorCategory.UNKNOWN

    @staticmethod
    def _compose_final_output(runs_by_agent: dict[str, AgentRun]) -> str:
        writer = runs_by_agent.get("writer")
        critic = runs_by_agent.get("critic")
        if writer and critic:
            return f"{writer.output.result}\n\nCritic:\n{critic.output.result}"
        if writer:
            return writer.output.result
        if runs_by_agent:
            return list(runs_by_agent.values())[-1].output.result
        return ""
