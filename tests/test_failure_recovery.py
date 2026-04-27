import pytest

from app.agents.base_agent import AgentRun
from app.agents.researcher import ResearchAgent
from app.core.config import Settings
from app.core.llm_router import LLMRouter
from app.models.schemas import AgentOutput, ResearchTopic, TaskStatus
from app.services import MemoryStore, Orchestrator, TaskManager


@pytest.mark.asyncio
async def test_llm_router_uses_local_when_providers_blacklisted() -> None:
    settings = Settings(
        simple_task_provider="openai",
        complex_task_provider="openai",
        default_provider="local",
        fallback_providers=["anthropic", "local"],
        provider_blacklist=["openai", "anthropic"],
    )
    router = LLMRouter(settings)

    _, usage, _ = await router.generate("hello", {"task": "hello", "agent": "writer"})

    assert usage.provider == "local"
    assert usage.total_tokens > 0


@pytest.mark.asyncio
async def test_step_failure_triggers_retry_then_success(monkeypatch) -> None:
    settings = Settings(
        simple_task_provider="local",
        complex_task_provider="local",
        fallback_providers=["local"],
        max_retries=1,
        retry_backoff_seconds=0,
    )
    task_manager = TaskManager(database_url="sqlite:///:memory:")
    memory = MemoryStore(settings)
    orchestrator = Orchestrator(settings, task_manager, memory)
    calls = {"count": 0}
    original = ResearchAgent.act

    async def flaky_act(self, task, context, plan):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("transient tool error")
        return await original(self, task, context, plan)

    monkeypatch.setattr(ResearchAgent, "act", flaky_act)
    task = await task_manager.create_task("生成一份关于多智能体系统的中文技术研究报告")

    result = await orchestrator.execute_task(task.task_id)

    assert result.status == TaskStatus.SUCCESS
    assert any(step.agent == "researcher" and step.retries == 1 for step in result.steps)


@pytest.mark.asyncio
async def test_critic_gap_triggers_follow_up_search_round(monkeypatch) -> None:
    settings = Settings(
        simple_task_provider="local",
        complex_task_provider="local",
        fallback_providers=["local"],
        max_retries=0,
    )
    task_manager = TaskManager(database_url="sqlite:///:memory:")
    memory = MemoryStore(settings)
    orchestrator = Orchestrator(settings, task_manager, memory)
    calls = {"count": 0}

    async def critic_once(self, task, context, plan):
        calls["count"] += 1
        sufficient = calls["count"] > 1
        follow_up = ResearchTopic(
            topic_id="follow-up-topic",
            question="为研究平台再寻找一个来源",
            search_queries=["研究平台 补充来源"],
            rationale="test follow-up",
        )
        return AgentRun(
            output=AgentOutput(
                thought="critic checked coverage",
                action="evaluate_evidence_sufficiency",
                result="sufficient" if sufficient else "needs more evidence",
                metadata={
                    "sufficient": sufficient,
                    "coverage_score": 1.0 if sufficient else 0.5,
                    "faithfulness_score": 1.0,
                    "answer_relevancy_score": 1.0 if sufficient else 0.5,
                    "missing_topics": [] if sufficient else ["topic-1-scope"],
                    "follow_up_topics": [] if sufficient else [follow_up.model_dump(mode="json")],
                    "notes": "test",
                },
            ),
            metadata={"sufficient": sufficient},
        )

    monkeypatch.setattr("app.agents.critic.CriticAgent.act", critic_once)
    task = await task_manager.create_task("撰写研究平台摘要")

    result = await orchestrator.execute_task(task.task_id)

    assert result.status == TaskStatus.SUCCESS
    assert calls["count"] == 2
    assert [step.agent for step in result.steps].count("critic") == 2
