from __future__ import annotations

from typing import Any

from app.agents.base_agent import AgentRun, BaseAgent
from app.models.schemas import AgentOutput, ResearchTopic, TaskGraph, TaskGraphStep


class PlannerAgent(BaseAgent):
    name = "planner"
    role = "将开放式研究问题拆解为 3-5 个可追溯子主题"

    async def plan(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        complexity = self.assess_complexity(task)
        graph = self.build_task_graph(task, complexity, context)
        return {
            "objective": "生成迭代式研究计划",
            "complexity": complexity,
            "task_graph": graph.model_dump(mode="json"),
            "research_topics": [topic.model_dump(mode="json") for topic in self.build_research_topics(task, context)],
            "success_criteria": [
                "独立检索 3-5 个聚焦研究主题",
                "最终报告中的关键结论都能映射到证据卡片和 URL 引用",
                "Critic 判断证据覆盖是否充分，不充分时生成补充检索主题",
            ],
        }

    async def act(self, task: str, context: dict[str, Any], plan: dict[str, Any]) -> AgentRun:
        prompt = (
            "你是迭代式深度研究系统中的 Planner。\n"
            f"用户问题：{task}\n"
            f"规划元数据：{context}\n"
            f"候选工作流：{plan}\n"
            "请用中文总结子研究主题、证据风险和检索策略。"
        )
        text, usage, routing = await self._generate(prompt, task, {**context, "complexity": plan["complexity"]})
        output = AgentOutput(
            thought="已将问题拆解为边界清晰的子主题，供 Researcher 并行检索并交由 Critic 审查。",
            action="decompose_research_question",
            result=text,
            metadata={
                "task_graph": plan["task_graph"],
                "research_topics": plan["research_topics"],
                "complexity": plan["complexity"],
            },
        )
        return AgentRun(output=output, llm_usage=[usage], routing_decisions=[routing], metadata={"complexity": plan["complexity"]})

    @staticmethod
    def assess_complexity(task: str) -> str:
        text = task.lower()
        complex_markers = [
            "open-ended",
            "report",
            "research",
            "compare",
            "why",
            "how",
            "trend",
            "strategy",
            "impact",
            "研究",
            "报告",
            "比较",
            "为什么",
            "如何",
            "趋势",
            "策略",
            "影响",
        ]
        if len(task.split()) > 18 or any(marker in text for marker in complex_markers):
            return "complex"
        return "simple"

    @staticmethod
    def build_research_topics(task: str, context: dict[str, Any] | None = None) -> list[ResearchTopic]:
        context = context or {}
        compact = " ".join(task.strip().split())
        templates = [
            ("scope", "澄清核心概念、定义和问题边界。"),
            ("evidence", "寻找经验证据、具体案例和可衡量信号。"),
            ("tradeoffs", "识别不同观点、约束条件和潜在失败模式。"),
            ("implementation", "梳理实践路径、工具链和工程实现细节。"),
            ("outlook", "评估影响、开放问题和后续趋势。"),
        ]
        requested_depth = str(context.get("research_depth", context.get("depth", "standard"))).lower()
        requested_topics = int(context.get("max_topics", 0) or 0)
        if requested_topics:
            topic_count = max(3, min(5, requested_topics))
        elif requested_depth == "deep":
            topic_count = 5
        else:
            topic_count = 3 if len(compact.split()) < 12 else 5
        topics = []
        for index, (slug, rationale) in enumerate(templates[:topic_count], start=1):
            question = f"{compact} - {rationale}"
            topics.append(
                ResearchTopic(
                    topic_id=f"topic-{index}-{slug}",
                    question=question,
                    search_queries=[
                        f"{compact} {slug} 证据",
                        f"{compact} {slug} 分析",
                    ],
                    rationale=rationale,
                )
            )
        return topics

    @staticmethod
    def build_task_graph(task: str, complexity: str, context: dict[str, Any]) -> TaskGraph:
        enabled = context.get("enabled_agents", {})
        steps: list[TaskGraphStep] = []

        def add(agent: str, goal: str, depends_on: list[int] | None = None) -> None:
            if enabled and enabled.get(agent, True) is False:
                return
            steps.append(
                TaskGraphStep(
                    step_id=len(steps) + 1,
                    agent=agent,
                    goal=goal,
                    depends_on=depends_on or [],
                )
            )

        topic_count = len(PlannerAgent.build_research_topics(task, context))
        for index in range(1, topic_count + 1):
            add("researcher", f"为研究主题 {index} 收集带 URL 追溯的证据")
        research_step_ids = [step.step_id for step in steps]
        add("critic", "评估证据充分性并判断是否需要追加检索", research_step_ids)
        add("writer", "基于已批准证据卡片撰写带引用的 Markdown 中文研究报告", [steps[-1].step_id] if steps else [])

        return TaskGraph(
            complexity=complexity,
            steps=steps,
            replan_reason=context.get("replan_reason"),
        )
