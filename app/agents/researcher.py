from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.agents.base_agent import AgentRun, BaseAgent
from app.models.schemas import AgentOutput, Citation, EvidenceCard, ResearchTopic


class ResearchAgent(BaseAgent):
    name = "researcher"
    role = "并行执行网页检索，并将来源转化为带 URL 追溯的证据卡片"

    async def plan(self, task: str, context: dict[str, Any]) -> dict[str, Any]:
        tools = ["web_search", "web_fetch"]
        if context.get("document_path"):
            tools.append("doc_reader")
        return {
            "objective": "为单个研究子主题收集段落级证据",
            "tools": tools,
            "queries": self._topic(context).search_queries or [task],
        }

    async def act(self, task: str, context: dict[str, Any], plan: dict[str, Any]) -> AgentRun:
        tool_calls = []
        topic = self._topic(context)
        source_entries: list[dict[str, Any]] = []
        max_sources = int(context.get("max_sources_per_query", 4))
        search_depth = str(context.get("search_depth", "advanced"))
        search_provider = str(context.get("web_search_provider", ""))

        for query in plan["queries"][:3]:
            search = await self.call_tool(
                "web_search",
                query,
                max_results=max_sources,
                search_depth=search_depth,
                provider=search_provider or None,
            )
            tool_calls.append(search)
            if not search.success:
                continue
            search_payload = json.loads(search.output)
            for result in search_payload.get("results", [])[:max_sources]:
                fetch = await self.call_tool("web_fetch", result["url"])
                if fetch.success:
                    tool_calls.append(fetch)
                    source_payload = json.loads(fetch.output)
                else:
                    # Single-page fetch failures should not abort the whole research topic.
                    source_payload = result
                paragraphs = source_payload.get("paragraphs") or result.get("paragraphs", [])
                title = source_payload.get("title") or result.get("title") or result["url"]
                for paragraph in paragraphs[:2]:
                    raw_text = str(paragraph.get("text") or "")
                    cleaned = self._clean_paragraph_text(raw_text)
                    if not cleaned or self._is_noise_text(cleaned):
                        continue
                    source_entries.append(
                        {
                            "url": result["url"],
                            "title": title,
                            "paragraph_id": paragraph["paragraph_id"],
                            "text": cleaned,
                            "score": float(result.get("score", 0.75)),
                            "provider": str(result.get("provider", source_payload.get("provider", "web"))),
                        }
                    )

        if context.get("document_path"):
            doc = await self.call_tool("doc_reader", str(context["document_path"]))
            tool_calls.append(doc)

        cards = self._build_preliminary_cards(topic, source_entries)
        topic_summary = ""
        llm_cards, topic_summary, usage, routing = await self._draft_cards_with_llm(task, topic, source_entries, context)
        if llm_cards:
            cards = llm_cards
        if not topic_summary:
            topic_summary = self._fallback_topic_summary(topic, cards)

        evidence = "\n".join(
            f"- {card.claim}：{card.summary} [{card.citation.title}#{card.citation.paragraph_id}]({card.citation.url})"
            for card in cards
        )
        output = AgentOutput(
            thought="检索结果已抓取并标准化为包含 URL 和段落 ID 的证据卡片。",
            action="search_fetch_extract_evidence_cards",
            result=topic_summary,
            metadata={
                "research_topic": topic.model_dump(mode="json"),
                "evidence": evidence,
                "evidence_cards": [card.model_dump(mode="json") for card in cards],
            },
        )
        return AgentRun(output=output, llm_usage=[usage], tool_calls=tool_calls, routing_decisions=[routing])

    @staticmethod
    def _topic(context: dict[str, Any]) -> ResearchTopic:
        raw = context.get("research_topic")
        if raw:
            return ResearchTopic.model_validate(raw)
        graph_step = context.get("graph_step") or {}
        goal = graph_step.get("goal") or "为用户问题收集证据。"
        return ResearchTopic(topic_id="topic-default", question=goal, search_queries=[goal], rationale="兜底研究主题")

    @staticmethod
    def _card_id(topic_id: str, url: str, paragraph_id: str) -> str:
        digest = hashlib.sha1(f"{topic_id}:{url}:{paragraph_id}".encode("utf-8")).hexdigest()[:12]
        return f"card-{digest}"

    @staticmethod
    def _clean_paragraph_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _is_noise_text(cls, text: str) -> bool:
        lowered = cls._clean_paragraph_text(text).lower()
        noise_signals = [
            "选择您的 cookie 首选项",
            "必要 cookie",
            "性能 cookie",
            "接受或拒绝",
            "如果您同意",
            "注册登录",
            "登录/注册",
            "跳转到主要内容",
            "文档建议反馈控制台",
            "cookie preferences",
            "accept or reject",
            "sign in",
            "log in",
        ]
        if any(signal in lowered for signal in noise_signals):
            return True
        navigation_hits = sum(
            token in lowered
            for token in ["首页", "社区", "关于", "常见问题", "控制台", "文档", "登录", "注册", "反馈"]
        )
        return navigation_hits >= 4 and len(text) < 500

    @classmethod
    def _build_preliminary_cards(cls, topic: ResearchTopic, source_entries: list[dict[str, Any]]) -> list[EvidenceCard]:
        cards: list[EvidenceCard] = []
        for entry in source_entries:
            summary = cls._summarize_paragraph(topic, entry["text"])
            claim = cls._build_claim(topic, summary)
            if not summary or cls._is_noise_text(summary):
                continue
            cards.append(
                EvidenceCard(
                    card_id=cls._card_id(topic.topic_id, entry["url"], entry["paragraph_id"]),
                    topic_id=topic.topic_id,
                    claim=claim,
                    summary=summary,
                    citation=Citation(
                        url=entry["url"],
                        title=entry["title"],
                        paragraph_id=entry["paragraph_id"],
                        snippet=entry["text"][:240],
                    ),
                    confidence=float(entry["score"]),
                    source_type=entry["provider"],
                )
            )
        return cards

    async def _draft_cards_with_llm(
        self,
        task: str,
        topic: ResearchTopic,
        source_entries: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> tuple[list[EvidenceCard] | None, str, Any | None, Any | None]:
        if not source_entries:
            return None, "", None, None

        source_lines = []
        for index, entry in enumerate(source_entries[:6], start=1):
            source_lines.append(
                f"{index}. 标题：{entry['title']}\n"
                f"URL：{entry['url']}\n"
                f"段落：{entry['paragraph_id']}\n"
                f"原文摘录：{entry['text'][:700]}"
            )

        prompt = (
            "你是研究助理。请基于研究主题和网页正文摘录，生成适合研究工作台展示的证据卡片。\n"
            "要求：\n"
            "1. summary 必须是面向当前研究主题的中文摘要，不是网页快照。\n"
            "2. claim 必须是短句形式的研究发现，不要写成“某来源支持某问题”。\n"
            "3. 不要复述页面导航、Cookie、登录提示或站点模板。\n"
            "4. 输出严格 JSON，不要加代码块。\n"
            "5. JSON 格式："
            '{"topic_overview":"...","cards":[{"source_url":"...","paragraph_id":"...","claim":"...","summary":"..."}]}\n'
            f"任务：{task}\n"
            f"研究主题：{topic.model_dump(mode='json')}\n"
            f"来源摘录：\n{chr(10).join(source_lines)}"
        )

        text, usage, routing = await self._generate(prompt, task, context)
        payload = self._extract_json_object(text)
        if not payload:
            return None, text, usage, routing

        overview = self._clean_paragraph_text(str(payload.get("topic_overview") or ""))
        cards_payload = payload.get("cards")
        if not isinstance(cards_payload, list):
            return None, overview, usage, routing

        source_map = {(entry["url"], entry["paragraph_id"]): entry for entry in source_entries}
        cards: list[EvidenceCard] = []
        for item in cards_payload:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("source_url") or ""), str(item.get("paragraph_id") or ""))
            entry = source_map.get(key)
            if not entry:
                continue
            claim = self._clean_paragraph_text(str(item.get("claim") or ""))
            summary = self._clean_paragraph_text(str(item.get("summary") or ""))
            if not claim or not summary or self._is_noise_text(summary):
                continue
            cards.append(
                EvidenceCard(
                    card_id=self._card_id(topic.topic_id, entry["url"], entry["paragraph_id"]),
                    topic_id=topic.topic_id,
                    claim=claim[:140],
                    summary=summary[:260],
                    citation=Citation(
                        url=entry["url"],
                        title=entry["title"],
                        paragraph_id=entry["paragraph_id"],
                        snippet=entry["text"][:240],
                    ),
                    confidence=float(entry["score"]),
                    source_type=entry["provider"],
                )
            )

        return (cards or None), overview, usage, routing

    @classmethod
    def _summarize_paragraph(cls, topic: ResearchTopic, text: str, max_chars: int = 220) -> str:
        sentences = cls._split_sentences(text)
        if not sentences:
            return text[:max_chars].rstrip("，、；： ")

        keywords = cls._topic_keywords(topic)
        ranked = sorted(
            ((cls._sentence_score(sentence, keywords, index), index, sentence) for index, sentence in enumerate(sentences)),
            reverse=True,
        )
        selected_indices = sorted(index for _, index, _ in ranked[:2])
        chosen = [sentences[index] for index in selected_indices]
        summary = " ".join(chosen).strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars].rstrip("，、；： ")
        return summary

    @staticmethod
    def _build_claim(topic: ResearchTopic, summary: str, max_chars: int = 120) -> str:
        text = summary[:max_chars].rstrip("，、；： ")
        if topic.rationale:
            return f"{topic.rationale}：{text}"
        return text

    @classmethod
    def _fallback_topic_summary(cls, topic: ResearchTopic, cards: list[EvidenceCard]) -> str:
        if not cards:
            return f"{topic.rationale or topic.question}：当前尚未收集到足够的有效证据。"
        leading = "；".join(card.claim for card in cards[:3])
        return f"{topic.rationale or topic.question}：{leading}"

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        parts = re.split(r"(?<=[。！？.!?])\s+|(?<=[。！？])|(?<=[.!?])(?=[A-Z0-9\u4e00-\u9fff])", text)
        return [part.strip() for part in parts if part and part.strip()]

    @staticmethod
    def _topic_keywords(topic: ResearchTopic) -> set[str]:
        raw = f"{topic.question} {topic.rationale}"
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_-]{3,}", raw.lower())
        return set(tokens)

    @classmethod
    def _sentence_score(cls, sentence: str, keywords: set[str], index: int) -> tuple[int, int, int]:
        lowered = sentence.lower()
        overlap = sum(keyword in lowered for keyword in keywords)
        # Prefer keyword-rich and earlier sentences to keep summaries stable and concise.
        return overlap, -index, -min(len(sentence), 400)

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
            stripped = re.sub(r"\n?```$", "", stripped)
        match = re.search(r"\{.*\}", stripped, flags=re.S)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
