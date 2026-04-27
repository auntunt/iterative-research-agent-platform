from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agents.base_agent import AgentRun
    from app.core.llm_router import LLMContext, LLMRouter


_COMPRESS_PROMPT = """请将以下内容压缩为不超过 {n} 句话的摘要，保留最重要的事实和结论，去掉冗余细节。
直接输出摘要内容，不要添加"摘要："等前缀。

内容：
{text}"""

_AGENT_SUMMARY_PROMPT = """请用 2-3 句话总结以下 {agent} 智能体的执行结果，重点包含：关键发现、使用的工具、得出的结论。
直接输出摘要，不要添加前缀。

执行结果：
{output}"""

_ROLLING_UPDATE_PROMPT = """已有摘要：
{existing}

新增事件：
{new_events}

请将新增事件的关键信息融入已有摘要，生成更新后的摘要（不超过 6 句话）。直接输出更新后的摘要。"""


class IncrementalSummarizer:
    """
    使用最小可用模型生成增量摘要，不走 ComplexityEstimator。

    所有摘要任务固定使用 nano tier 对应的模型，以降低成本。
    """

    def __init__(self, llm_router: LLMRouter, summary_model_key: str = "openai_nano") -> None:
        self.llm_router = llm_router
        self.summary_model_key = summary_model_key

    async def compress_text(self, text: str, target_sentences: int = 3) -> str:
        """将任意文本压缩为指定句数的摘要。"""
        if not text.strip():
            return ""
        prompt = _COMPRESS_PROMPT.format(n=target_sentences, text=text[:12_000])
        result, _, _rd = await self.llm_router.generate(prompt, self._nano_context("summarizer"))
        return result.strip()

    async def summarize_agent_run(self, agent_name: str, run: AgentRun) -> str:
        """agent 节点完成后生成执行摘要（供 rolling_update 使用）。"""
        output_text = run.output.result or str(run.output.model_dump())
        tool_info = ""
        if run.tool_calls:
            names = [tc.tool for tc in run.tool_calls]
            tool_info = f"（使用工具：{', '.join(names)}）"

        prompt = _AGENT_SUMMARY_PROMPT.format(
            agent=agent_name,
            output=f"{tool_info}\n{output_text[:8_000]}",
        )
        result, _, _rd = await self.llm_router.generate(prompt, self._nano_context(agent_name))
        return result.strip()

    async def rolling_update(
        self,
        existing_summary: str,
        new_events: list[dict[str, Any]],
    ) -> str:
        """将新事件列表融入现有滚动摘要，生成更新版本。"""
        if not new_events:
            return existing_summary

        events_text = "\n".join(
            f"- [{e.get('event', 'event')}] {str(e)[:300]}" for e in new_events[-10:]
        )
        prompt = _ROLLING_UPDATE_PROMPT.format(
            existing=existing_summary or "（暂无摘要）",
            new_events=events_text,
        )
        result, _, _rd = await self.llm_router.generate(prompt, self._nano_context("summarizer"))
        return result.strip()

    def _nano_context(self, agent: str) -> LLMContext:
        from app.core.llm_router import LLMContext

        return LLMContext(
            task="summarization",
            agent=agent,
            complexity="simple",
            metadata={"force_provider": self.summary_model_key},
        )
