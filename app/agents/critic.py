from __future__ import annotations

from collections import Counter
from typing import Any

from app.agents.base_agent import AgentRun, BaseAgent
from app.models.schemas import AgentOutput, CriticAssessment, EvidenceCard, ResearchTopic


class CriticAgent(BaseAgent):
    name = "critic"
    role = "评估证据充分性、忠实度，并判断是否需要追加检索"

    async def plan(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "objective": "判断当前证据是否足以进入报告撰写阶段",
            "checks": [
                "每个计划主题都有足够证据卡片",
                "引用包含 URL 和段落 ID",
                "结论能被检索片段支撑",
                "证据缺口会被转化为后续研究主题",
            ],
        }

    async def act(self, task: str, context: dict[str, Any], plan: dict[str, Any]) -> AgentRun:
        topics = [ResearchTopic.model_validate(topic) for topic in context.get("research_topics", [])]
        cards = [EvidenceCard.model_validate(card) for card in context.get("evidence_cards", [])]
        min_cards = int(context.get("min_evidence_per_topic", 2))
        assessment = self._assess(topics, cards, min_cards)
        prompt = (
            "你是规则严格的 Critic，请用中文评估当前证据集。\n"
            f"任务：{task}\n"
            f"计划：{plan}\n"
            f"证据数量：{dict(Counter(card.topic_id for card in cards))}\n"
            f"初步评估：{assessment.model_dump(mode='json')}\n"
            "请返回简洁的审查意见。"
        )
        text, usage, routing = await self._generate(prompt, task, context)
        output = AgentOutput(
            thought="已从主题覆盖、引用完整性和答案相关性三个维度评估证据。",
            action="evaluate_evidence_sufficiency",
            result=text,
            metadata=assessment.model_dump(mode="json"),
        )
        return AgentRun(output=output, llm_usage=[usage], routing_decisions=[routing], metadata=assessment.model_dump(mode="json"))

    @staticmethod
    def _assess(topics: list[ResearchTopic], cards: list[EvidenceCard], min_cards: int) -> CriticAssessment:
        by_topic = Counter(card.topic_id for card in cards)
        missing = [topic.topic_id for topic in topics if by_topic[topic.topic_id] < min_cards]
        url_count = len({card.citation.url for card in cards})
        topic_count = max(1, len(topics))
        covered_topics = topic_count - len(missing)
        coverage = covered_topics / topic_count
        citation_integrity = sum(bool(card.citation.url and card.citation.paragraph_id) for card in cards) / max(1, len(cards))
        faithfulness = min(1.0, citation_integrity * (url_count / max(1, len(cards))))
        sufficient = not missing and citation_integrity >= 0.95 and len(cards) >= topic_count * min_cards

        follow_ups = []
        topic_by_id = {topic.topic_id: topic for topic in topics}
        for index, topic_id in enumerate(missing, start=1):
            base = topic_by_id[topic_id]
            follow_ups.append(
                ResearchTopic(
                    topic_id=f"follow-up-{index}-{topic_id}",
                    question=f"为以下主题寻找更强证据：{base.question}",
                    search_queries=[f"{base.question} 补充证据", f"{base.question} 反例 或 不同观点"],
                    rationale="Critic 认为该主题证据不足，需要追加检索。",
                )
            )

        return CriticAssessment(
            sufficient=sufficient,
            coverage_score=round(coverage, 3),
            faithfulness_score=round(faithfulness, 3),
            answer_relevancy_score=round(min(1.0, coverage * 0.7 + citation_integrity * 0.3), 3),
            missing_topics=missing,
            follow_up_topics=follow_ups,
            notes="证据已经足够。" if sufficient else "进入最终写作前需要追加检索。",
        )
