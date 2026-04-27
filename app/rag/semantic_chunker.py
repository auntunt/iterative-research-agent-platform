from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.token_counter import count_tokens

if TYPE_CHECKING:
    from app.rag.embedder import Embedder

# 中英文混合分句正则
# 中文句尾：。！？……
# 英文句尾：.!? 后跟空格或行尾（排除小数点、URL）
_SENTENCE_SPLIT = re.compile(
    r"(?<=[。！？…])"
    r"|(?<=[.!?])(?=\s|$)"
)

# 短于此 token 数的句子合并到前一组，避免碎片 chunk
_MIN_SENTENCE_TOKENS = 10


@dataclass
class SemanticChunk:
    idx: int
    text: str
    token_count: int
    start_sentence: int
    end_sentence: int


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(text.strip())
    sentences: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if sentences and count_tokens(part) < _MIN_SENTENCE_TOKENS:
            sentences[-1] = sentences[-1] + part
        else:
            sentences.append(part)
    return sentences


class SemanticChunker:
    """
    基于余弦相似度断点的语义分块器。

    算法：
    1. 按标点分句（中英文混合）
    2. 批量嵌入所有句子
    3. 计算相邻句子余弦相似度
    4. 在相似度 < threshold 处断开（语义边界）
    5. 合并同组句子为 chunk，过长者递归二分
    """

    def __init__(
        self,
        embedder: Embedder,
        threshold: float = 0.75,
        max_chunk_tokens: int = 400,
        model_id: str = "gpt-4o",
    ) -> None:
        self.embedder = embedder
        self.threshold = threshold
        self.max_chunk_tokens = max_chunk_tokens
        self.model_id = model_id

    async def chunk(self, text: str) -> list[SemanticChunk]:
        """将文本切分为语义连贯的 chunk 列表。"""
        sentences = _split_sentences(text)
        if len(sentences) <= 1:
            tc = count_tokens(text, self.model_id)
            return [SemanticChunk(idx=0, text=text, token_count=tc, start_sentence=0, end_sentence=0)]

        embeddings = await self.embedder.embed_batch(sentences)
        breakpoints = self._find_breakpoints(embeddings)
        groups = self._group_sentences(sentences, breakpoints)
        chunks = self._build_chunks(groups)
        return chunks

    def _find_breakpoints(self, embeddings: list[list[float]]) -> list[int]:
        breakpoints: list[int] = []
        for i in range(len(embeddings) - 1):
            sim = _cosine_similarity(embeddings[i], embeddings[i + 1])
            if sim < self.threshold:
                breakpoints.append(i + 1)
        return breakpoints

    @staticmethod
    def _group_sentences(sentences: list[str], breakpoints: list[int]) -> list[tuple[int, int, list[str]]]:
        """返回 (start_idx, end_idx, sentences_in_group) 的列表。"""
        groups: list[tuple[int, int, list[str]]] = []
        bp_set = set(breakpoints)
        current_start = 0
        current: list[str] = []

        for i, sentence in enumerate(sentences):
            if i in bp_set and current:
                groups.append((current_start, i - 1, current))
                current_start = i
                current = [sentence]
            else:
                current.append(sentence)

        if current:
            groups.append((current_start, len(sentences) - 1, current))

        return groups

    def _build_chunks(
        self, groups: list[tuple[int, int, list[str]]]
    ) -> list[SemanticChunk]:
        chunks: list[SemanticChunk] = []
        for start, end, sentences in groups:
            text = "".join(sentences)
            tc = count_tokens(text, self.model_id)
            if tc <= self.max_chunk_tokens:
                chunks.append(
                    SemanticChunk(
                        idx=len(chunks),
                        text=text,
                        token_count=tc,
                        start_sentence=start,
                        end_sentence=end,
                    )
                )
            else:
                # 过长：二分递归
                mid = len(sentences) // 2
                sub_groups = [
                    (start, start + mid - 1, sentences[:mid]),
                    (start + mid, end, sentences[mid:]),
                ]
                chunks.extend(self._build_chunks(sub_groups))
        return chunks
