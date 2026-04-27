from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.token_counter import count_tokens

if TYPE_CHECKING:
    from app.context.summarizer import IncrementalSummarizer


@dataclass
class CompressionStats:
    compression_count: int = 0
    tokens_saved: int = 0
    original_token_count: int = 0
    current_token_count: int = 0


class ContextWindowManager:
    """
    管理传递给 LLM 的上下文消息列表，确保总 token 数不超过窗口上限。

    当消息超出预算时，将最旧的消息批量送给 summarizer 压缩为摘要句，
    摘要以 system 消息的形式插回列表首位。
    """

    def __init__(
        self,
        model_id: str = "gpt-4o",
        max_tokens: int = 80_000,
        reserve_tokens: int = 4_096,
    ) -> None:
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.reserve_tokens = reserve_tokens
        self._stats: dict[str, CompressionStats] = {}

    @property
    def budget(self) -> int:
        return self.max_tokens - self.reserve_tokens

    def count(self, text: str) -> int:
        return count_tokens(text, self.model_id)

    def fits(self, text: str, remaining: int) -> bool:
        return self.count(text) <= remaining

    async def fit_context(
        self,
        task_id: str,
        context_str: str,
        summarizer: IncrementalSummarizer,
    ) -> tuple[str, bool]:
        """
        确保单条 context 字符串不超过 budget。

        如果超出，用 summarizer 压缩后返回压缩版本。
        返回值：(可用的 context 字符串, 是否发生了压缩)
        """
        token_count = self.count(context_str)
        stats = self._stats.setdefault(task_id, CompressionStats())
        stats.original_token_count = token_count

        if token_count <= self.budget:
            stats.current_token_count = token_count
            return context_str, False

        # 超出预算，压缩
        compressed = await summarizer.compress_text(context_str, target_sentences=5)
        compressed_tokens = self.count(compressed)
        saved = token_count - compressed_tokens

        stats.compression_count += 1
        stats.tokens_saved += saved
        stats.current_token_count = compressed_tokens

        return compressed, True

    async def fit_messages(
        self,
        task_id: str,
        messages: list[dict[str, Any]],
        summarizer: IncrementalSummarizer,
    ) -> list[dict[str, Any]]:
        """
        对标准 messages list（[{"role": ..., "content": ...}]）做滑动窗口管理。

        从最新消息向前保留；超出部分发给 summarizer，摘要以 system 消息插入首位。
        """
        stats = self._stats.setdefault(task_id, CompressionStats())
        remaining = self.budget
        kept: list[dict[str, Any]] = []
        overflow: list[dict[str, Any]] = []

        for msg in reversed(messages):
            tokens = self.count(msg.get("content", ""))
            if remaining - tokens >= 0:
                kept.insert(0, msg)
                remaining -= tokens
            else:
                overflow.insert(0, msg)

        if overflow:
            overflow_text = "\n".join(m.get("content", "") for m in overflow)
            summary = await summarizer.compress_text(overflow_text, target_sentences=4)
            summary_tokens = self.count(summary)
            saved = self.count(overflow_text) - summary_tokens

            stats.compression_count += 1
            stats.tokens_saved += saved

            kept.insert(0, {"role": "system", "content": f"[早期对话摘要]: {summary}"})

        stats.current_token_count = sum(self.count(m.get("content", "")) for m in kept)
        return kept

    def get_stats(self, task_id: str) -> CompressionStats:
        return self._stats.get(task_id, CompressionStats())
