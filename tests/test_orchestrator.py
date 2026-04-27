import json

import pytest

from app.core.config import Settings
from app.models.schemas import TaskStatus
from app.agents.researcher import ResearchAgent
from app.services import MemoryStore, Orchestrator, TaskManager
from app.tools.web_fetch import WebFetchTool
from app.tools.web_search import WebSearchTool


@pytest.mark.asyncio
async def test_orchestrator_runs_full_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(self, tool_input: str, **kwargs: object) -> str:
        return json.dumps(
            {
                "query": tool_input,
                "provider": "duckduckgo",
                "results": [
                    {
                        "title": f"Result for {tool_input[:20]}",
                        "url": f"https://example.com/{abs(hash(tool_input)) % 10000}",
                        "paragraphs": [{"paragraph_id": "snippet-1", "text": "Search snippet for orchestrator test."}],
                        "score": 0.88,
                        "provider": "duckduckgo",
                    }
                ],
            }
        )

    async def fake_fetch(self, tool_input: str, **kwargs: object) -> str:
        return json.dumps(
            {
                "url": tool_input,
                "title": "Fetched source",
                "paragraphs": [
                    {"paragraph_id": "p1", "text": "This source describes a multi-agent research workflow with citations."},
                    {"paragraph_id": "p2", "text": "It also explains critic evaluation, evidence gaps, and follow-up loops."},
                ],
                "provider": "http_fetch",
            }
        )

    monkeypatch.setattr(WebSearchTool, "run", fake_search)
    monkeypatch.setattr(WebFetchTool, "run", fake_fetch)

    settings = Settings(
        simple_task_provider="local",
        complex_task_provider="local",
        fallback_providers=["local"],
        max_retries=1,
    )
    task_manager = TaskManager()
    memory = MemoryStore()
    orchestrator = Orchestrator(settings, task_manager, memory)

    task = await task_manager.create_task("生成一份关于多智能体系统的中文技术研究报告")
    result = await orchestrator.execute_task(task.task_id)

    assert result.status == TaskStatus.SUCCESS
    assert result.result
    agents = [step.agent for step in result.steps]
    assert agents[0] == "planner"
    assert agents.count("researcher") >= 3
    assert "critic" in agents
    assert agents[-1] == "writer"
    assert result.metrics.total_steps == len(result.steps)
    assert result.metrics.successful_steps == len(result.steps)
    assert result.metrics.llm_calls == len(result.steps)
    assert "引用索引" in result.result
    assert "写作审查" not in result.result
    assert "Critic 给出的" not in result.result
    assert "topic-1-scope" not in result.result
    logs = await task_manager.get_logs(task.task_id, limit=100)
    graph_nodes = {entry.payload.get("node") for entry in logs if entry.event == "langgraph_node_completed"}
    assert {"plan", "research", "critic", "write"}.issubset(graph_nodes)


@pytest.mark.asyncio
async def test_orchestrator_skips_single_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(self, tool_input: str, **kwargs: object) -> str:
        return json.dumps(
            {
                "query": tool_input,
                "provider": "duckduckgo",
                "results": [
                    {
                        "title": "Blocked source",
                        "url": "https://example.com/blocked",
                        "paragraphs": [],
                        "score": 0.4,
                        "provider": "duckduckgo",
                    },
                    {
                        "title": "Healthy source",
                        "url": "https://example.com/healthy",
                        "paragraphs": [{"paragraph_id": "snippet-1", "text": "Fallback search snippet."}],
                        "score": 0.9,
                        "provider": "duckduckgo",
                    },
                ],
            }
        )

    async def fake_fetch(self, tool_input: str, **kwargs: object) -> str:
        if tool_input.endswith("/blocked"):
            raise RuntimeError("Client error '403 Forbidden' for url 'https://example.com/blocked'")
        return json.dumps(
            {
                "url": tool_input,
                "title": "Fetched source",
                "paragraphs": [
                    {"paragraph_id": "p1", "text": "This healthy source still provides enough evidence to keep the workflow moving."},
                    {"paragraph_id": "p2", "text": "The researcher should skip blocked pages and continue processing remaining results."},
                ],
                "provider": "http_fetch",
            }
        )

    monkeypatch.setattr(WebSearchTool, "run", fake_search)
    monkeypatch.setattr(WebFetchTool, "run", fake_fetch)

    settings = Settings(
        simple_task_provider="local",
        complex_task_provider="local",
        fallback_providers=["local"],
        max_retries=1,
    )
    task_manager = TaskManager()
    memory = MemoryStore()
    orchestrator = Orchestrator(settings, task_manager, memory)

    task = await task_manager.create_task("研究 agent 沙箱的关键实现与风险")
    result = await orchestrator.execute_task(task.task_id)

    assert result.status == TaskStatus.SUCCESS
    assert result.result
    researcher_steps = [step for step in result.steps if step.agent == "researcher"]
    assert researcher_steps
    assert any("Fetched source" in (step.output or "") for step in researcher_steps)


@pytest.mark.asyncio
async def test_orchestrator_filters_noise_evidence_before_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(self, tool_input: str, **kwargs: object) -> str:
        return json.dumps(
            {
                "query": tool_input,
                "provider": "duckduckgo",
                "results": [
                    {
                        "title": "Noisy source",
                        "url": "https://example.com/noisy",
                        "paragraphs": [{"paragraph_id": "snippet-1", "text": "选择您的 Cookie 首选项 注册登录 社区 首页 关于 常见问题"}],
                        "score": 0.5,
                        "provider": "duckduckgo",
                    },
                    {
                        "title": "Healthy source",
                        "url": "https://example.com/healthy",
                        "paragraphs": [{"paragraph_id": "snippet-1", "text": "Agent 沙箱用于隔离代码执行环境并降低工具调用带来的系统风险。"}],
                        "score": 0.9,
                        "provider": "duckduckgo",
                    },
                ],
            }
        )

    async def fake_fetch(self, tool_input: str, **kwargs: object) -> str:
        if tool_input.endswith("/noisy"):
            return json.dumps(
                {
                    "url": tool_input,
                    "title": "Noisy page",
                    "paragraphs": [
                        {"paragraph_id": "p1", "text": "选择您的 Cookie 首选项 我们使用必要 Cookie 和类似工具提供我们的网站和服务。"},
                        {"paragraph_id": "p2", "text": "注册登录 社区 首页 关于 常见问题 文档建议反馈控制台"},
                    ],
                    "provider": "http_fetch",
                }
            )
        return json.dumps(
            {
                "url": tool_input,
                "title": "Healthy source",
                "paragraphs": [
                    {"paragraph_id": "p1", "text": "Agent 沙箱用于隔离代码执行环境并降低工具调用带来的系统风险。"},
                    {"paragraph_id": "p2", "text": "工程实现通常结合容器、微虚拟机、网络限制和权限控制。"},
                ],
                "provider": "http_fetch",
            }
        )

    monkeypatch.setattr(WebSearchTool, "run", fake_search)
    monkeypatch.setattr(WebFetchTool, "run", fake_fetch)

    settings = Settings(
        simple_task_provider="local",
        complex_task_provider="local",
        fallback_providers=["local"],
        max_retries=1,
    )
    task_manager = TaskManager()
    memory = MemoryStore()
    orchestrator = Orchestrator(settings, task_manager, memory)

    task = await task_manager.create_task("研究 agent 沙箱技术")
    result = await orchestrator.execute_task(task.task_id)

    assert result.status == TaskStatus.SUCCESS
    assert "Cookie" not in result.result
    assert "注册登录" not in result.result
    assert "爬虫噪音" not in result.result


def test_researcher_summary_is_distilled_not_raw_snapshot() -> None:
    topic = ResearchAgent._topic(
        {
            "research_topic": {
                "topic_id": "topic-1-scope",
                "question": "agent沙箱技术 - 澄清核心概念、定义和问题边界。",
                "search_queries": [],
                "rationale": "澄清核心概念、定义和问题边界",
            }
        }
    )
    raw = (
        "检索增强生成（RAG）通过注入外部知识提升了大语言模型的事实性，但在需要多步推理的问题上表现不佳。"
        "相反，以推理为导向的方法常常产生幻觉或事实基础错误。"
        "本综述从统一的推理-检索视角综合了这两个研究方向，并讨论了协同式 RAG-推理框架。"
    )

    summary = ResearchAgent._summarize_paragraph(topic, raw, max_chars=120)
    claim = ResearchAgent._build_claim(topic, summary)

    assert len(summary) <= 120
    assert summary != raw
    assert "澄清核心概念、定义和问题边界" in claim


def test_writer_topic_section_reads_like_report_paragraph() -> None:
    from app.agents.writer import WritingAgent
    from app.models.schemas import Citation, CriticAssessment, EvidenceCard, ResearchTopic

    cards = [
        EvidenceCard(
            card_id="card-1",
            topic_id="topic-1-scope",
            claim="Agent 沙箱的核心作用是隔离高风险工具执行",
            summary="相关来源指出，沙箱通过文件系统、网络和权限边界把模型生成代码限制在受控环境内。",
            citation=Citation(url="https://example.com/1", title="Source One", paragraph_id="p1", snippet="raw one"),
        ),
        EvidenceCard(
            card_id="card-2",
            topic_id="topic-1-scope",
            claim="行业实现正在从容器隔离走向更强的微虚拟机方案",
            summary="多篇资料将 Kata Containers 和 Firecracker 视为兼顾安全性与工程可用性的候选路径。",
            citation=Citation(url="https://example.com/2", title="Source Two", paragraph_id="p2", snippet="raw two"),
        ),
    ]

    citation_numbers = WritingAgent._citation_numbers(cards)
    section = WritingAgent._topic_section(
        type("Topic", (), {"rationale": "澄清核心概念", "question": "topic question"})(),
        cards,
        citation_numbers,
    )

    assert len(section) == 2
    assert "Agent 沙箱的核心作用是隔离高风险工具执行" in section[0]
    assert "[1]" in section[0]
    assert "进一步来看" in section[1]

    report = WritingAgent._compose_report(
        "研究 agent 沙箱技术",
        [
            ResearchTopic(
                topic_id="topic-1-scope",
                question="agent沙箱技术 - 澄清核心概念、定义和问题边界。",
                rationale="澄清核心概念、定义和问题边界",
                search_queries=[],
            )
        ],
        cards,
        CriticAssessment(coverage_score=1.0, faithfulness_score=0.9, answer_relevancy_score=1.0),
    )

    assert "### 澄清核心概念、定义和问题边界" in report
    assert "topic-1-scope" not in report
    assert "Critic 给出的" not in report
