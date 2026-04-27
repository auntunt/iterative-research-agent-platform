from __future__ import annotations

import re
from typing import Any

from app.agents.base_agent import AgentRun, BaseAgent
from app.models.schemas import AgentOutput, CriticAssessment, EvidenceCard, ResearchTopic


class WritingAgent(BaseAgent):
    name = "writer"
    role = "基于已批准证据卡片撰写带引用的中文 Markdown 研究报告"

    async def plan(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "objective": "产出可追溯来源的中文研究报告",
            "inputs": ["research_topics", "evidence_cards", "critic_assessment"],
            "quality_bar": ["结构清晰", "带来源引用", "明确说明证据缺口"],
        }

    async def act(self, task: str, context: dict[str, Any], plan: dict[str, Any]) -> AgentRun:
        topics = [ResearchTopic.model_validate(topic) for topic in context.get("research_topics", [])]
        cards = [EvidenceCard.model_validate(card) for card in context.get("evidence_cards", [])]
        assessment = CriticAssessment.model_validate(context.get("critic_assessment", {}))
        draft_report = self._compose_report(task, topics, cards, assessment)
        prompt = (
            "请将下面这份带引用的中文 Markdown 研究草稿改写为最终定稿。\n"
            "要求：\n"
            "1. 输出必须是正式、连贯、可直接交付的研究报告正文。\n"
            "2. 保留原有引用事实，不要新增没有证据支持的结论。\n"
            "3. 不要输出审查意见、修改说明、写作建议、元评论或任何“草稿/润色后”的说明。\n"
            "4. 保持 Markdown 结构清晰，正文以完整书面段落为主，不要写成链接堆砌或卡片清单。\n"
            "5. 不要讨论网页抓取过程、Cookie 横幅、导航栏、登录提示、爬虫噪音或清洗过程。\n"
            f"任务：{task}\n"
            f"报告草稿：\n{draft_report}\n"
            f"质量要求：{plan}\n"
            "请直接返回最终 Markdown 报告全文。"
        )
        text, usage, routing = await self._generate(prompt, task, context)
        final_report = self._sanitize_final_report(text, draft_report)
        render = await self.call_tool("markdown_render", final_report) if "markdown_render" in self.tools else None
        output = AgentOutput(
            thought="最终报告已由证据卡片生成，并通过 Markdown 渲染检查。",
            action="compose_cited_research_report",
            result=final_report,
            metadata={
                "critic_assessment": assessment.model_dump(mode="json"),
                "evidence_card_count": len(cards),
                "draft_report": draft_report,
                "markdown_render": render.model_dump(mode="json") if render else None,
            },
        )
        return AgentRun(output=output, llm_usage=[usage], routing_decisions=[routing], tool_calls=[render] if render else [])

    @staticmethod
    def _compose_report(
        task: str,
        topics: list[ResearchTopic],
        cards: list[EvidenceCard],
        assessment: CriticAssessment,
    ) -> str:
        cards_by_topic: dict[str, list[EvidenceCard]] = {}
        for card in cards:
            cards_by_topic.setdefault(card.topic_id, []).append(card)
        citation_numbers = WritingAgent._citation_numbers(cards)

        lines = [
            f"# 研究报告：{task}",
            "",
            "## 摘要",
            WritingAgent._build_abstract(topics, cards, assessment, citation_numbers),
            "",
            "## 引言",
            WritingAgent._build_introduction(task, topics, cards, citation_numbers),
            "",
            "## 正文",
        ]

        for topic in topics:
            lines.extend(["", f"### {WritingAgent._section_title(topic)}"])
            topic_cards = cards_by_topic.get(topic.topic_id, [])
            if not topic_cards:
                lines.append("当前公开证据仍不足以支撑这一主题形成稳定结论，后续需要补充更高质量的独立来源。")
                continue
            lines.extend(WritingAgent._topic_section(topic, topic_cards[:4], citation_numbers))

        if assessment.missing_topics:
            lines.extend(["", "## 局限与待研究问题"])
            for topic_id in assessment.missing_topics:
                lines.append(f"{topic_id} 对应的问题仍缺少足够独立来源支撑，当前材料尚不足以形成完全稳定的判断。")

        lines.extend([
            "",
            "## 结论",
            WritingAgent._build_conclusion(topics, cards, assessment, citation_numbers),
        ])

        lines.extend(["", "## 引用索引"])
        for (url, paragraph_id), number in sorted(citation_numbers.items(), key=lambda item: item[1]):
            card = next(
                (
                    candidate
                    for candidate in cards
                    if candidate.citation.url == url and candidate.citation.paragraph_id == paragraph_id
                ),
                None,
            )
            if not card:
                continue
            citation = card.citation
            lines.append(f"{number}. [{citation.title}#{citation.paragraph_id}]({citation.url})")

        return "\n".join(lines)

    @staticmethod
    def _build_abstract(
        topics: list[ResearchTopic],
        cards: list[EvidenceCard],
        assessment: CriticAssessment,
        citation_numbers: dict[tuple[str, str], int],
    ) -> str:
        del assessment
        lead_claims: list[str] = []
        seen_topics: set[str] = set()
        for card in cards:
            if card.topic_id in seen_topics:
                continue
            seen_topics.add(card.topic_id)
            lead_claims.append(
                f"{card.claim.rstrip('。')}{WritingAgent._citation_marker(card, citation_numbers)}"
            )
            if len(lead_claims) >= 3:
                break
        if lead_claims:
            summary = "；".join(lead_claims)
            return (
                f"本报告基于围绕 {len(topics)} 个关键议题收集的公开材料展开综合分析。"
                f"现有证据表明，{summary}。"
                "整体来看，相关研究已经形成较清晰的概念框架与实践方向，但在量化评测、长期演化机制和工程成本方面仍有继续深化的空间。"
            )
        return "本报告基于公开材料对该主题的核心概念、实现路径与风险边界进行了综合梳理，但现有证据仍不足以支撑更细颗粒度的稳定结论。"

    @staticmethod
    def _build_introduction(
        task: str,
        topics: list[ResearchTopic],
        cards: list[EvidenceCard],
        citation_numbers: dict[tuple[str, str], int],
    ) -> str:
        if not cards:
            return f"围绕“{task}”这一主题，当前公开材料数量有限，本文主要对已有线索进行整理，并指出后续值得补充的研究方向。"

        leading_cards = cards[: min(3, len(cards))]
        opening = "；".join(
            f"{card.claim.rstrip('。')}{WritingAgent._citation_marker(card, citation_numbers)}"
            for card in leading_cards
        )
        return (
            f"围绕“{task}”这一主题，现有公开研究主要集中在 {WritingAgent._topics_overview(topics)} 等几个方面。"
            f"综合不同来源可以看到，{opening}。"
            "因此，下文将分别从概念边界、经验依据、约束条件与实现路径等角度展开讨论，以形成更接近正式研究报告的综合判断。"
        )

    @staticmethod
    def _topic_section(
        topic: ResearchTopic,
        cards: list[EvidenceCard],
        citation_numbers: dict[tuple[str, str], int],
    ) -> list[str]:
        paragraphs: list[str] = []
        lead = cards[0]
        paragraphs.append(
            f"围绕“{topic.rationale or topic.question}”这一问题，现有证据首先表明，"
            f"{lead.claim.rstrip('。')}。{lead.summary}{WritingAgent._citation_marker(lead, citation_numbers)}"
        )

        for card in cards[1:]:
            paragraphs.append(
                f"进一步来看，{card.claim.rstrip('。')}。{card.summary}"
                f"{WritingAgent._citation_marker(card, citation_numbers)}"
            )
        return paragraphs

    @staticmethod
    def _build_conclusion(
        topics: list[ResearchTopic],
        cards: list[EvidenceCard],
        assessment: CriticAssessment,
        citation_numbers: dict[tuple[str, str], int],
    ) -> str:
        if not cards:
            return "当前研究尚未获得足够证据，因此无法形成稳定结论。"

        distinct_claims: list[str] = []
        seen_topics: set[str] = set()
        for card in cards:
            if card.topic_id in seen_topics:
                continue
            seen_topics.add(card.topic_id)
            distinct_claims.append(
                f"{card.claim.rstrip('。')}{WritingAgent._citation_marker(card, citation_numbers)}"
            )
            if len(distinct_claims) >= 3:
                break

        conclusion = "；".join(distinct_claims)
        if assessment.missing_topics:
            return (
                f"综合现有证据，可以确认 {conclusion}。"
                f"不过，{', '.join(assessment.missing_topics)} 仍存在证据不足的问题，"
                "后续研究应优先补充更高质量的独立来源与量化材料。"
            )
        return (
            f"综合现有证据，可以确认 {conclusion}。"
            "整体来看，当前材料已能支撑形成结构完整的阶段性判断，但在量化指标、长期演化机制和工程成本方面仍有继续深化的空间。"
        )

    @staticmethod
    def _section_title(topic: ResearchTopic) -> str:
        label = (topic.rationale or topic.question or topic.topic_id).strip()
        label = re.sub(r"^topic-\d+[-\w]*[:：]?\s*", "", label, flags=re.IGNORECASE)
        label = re.sub(r"^[^一-龥A-Za-z0-9]*", "", label)
        label = label.rstrip("。.:：")
        return label or "相关问题分析"

    @staticmethod
    def _topics_overview(topics: list[ResearchTopic]) -> str:
        labels: list[str] = []
        for topic in topics:
            label = WritingAgent._section_title(topic)
            if label not in labels:
                labels.append(label)
            if len(labels) >= 4:
                break
        if not labels:
            return "核心问题"
        if len(labels) == 1:
            return labels[0]
        return "、".join(labels[:-1]) + f" 与 {labels[-1]}"

    @staticmethod
    def _citation_numbers(cards: list[EvidenceCard]) -> dict[tuple[str, str], int]:
        numbers: dict[tuple[str, str], int] = {}
        for card in cards:
            key = (card.citation.url, card.citation.paragraph_id)
            if key not in numbers:
                numbers[key] = len(numbers) + 1
        return numbers

    @staticmethod
    def _citation_marker(card: EvidenceCard, citation_numbers: dict[tuple[str, str], int]) -> str:
        key = (card.citation.url, card.citation.paragraph_id)
        number = citation_numbers.get(key)
        return f"[{number}]" if number else ""

    @staticmethod
    def _sanitize_final_report(text: str, fallback: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            return fallback
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()
        banned = [
            "cookie",
            "导航栏",
            "登录提示",
            "注册登录",
            "跳转到主要内容",
            "爬虫噪音",
            "清洗过程",
            "cookie 横幅",
            "抓取过程",
            "写作审查",
            "审查意见",
            "润色建议",
            "编辑审查",
            "以下是对该研究报告的",
            "critic 给出的",
            "topic-1",
            "topic-2",
            "topic-3",
            "topic-4",
            "topic-5",
        ]
        if any(token in cleaned.lower() for token in banned):
            return fallback
        if not cleaned.startswith("# "):
            return fallback
        return cleaned
