from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.core.config import Settings

_OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"
_BATCH_SIZE = 10


class Embedder:
    """
    统一嵌入接口。

    - 有 OpenAI API Key → text-embedding-3-small (1536 维)
    - 否则 → sentence-transformers 本地模型（延迟加载，首次调用时初始化）
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._local_model: object | None = None
        self._dimension: int | None = None

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._settings.openai_api_key:
            return await self._openai_embed_batch(texts)
        return await self._local_embed_batch(texts)

    async def dimension(self) -> int:
        if self._dimension is not None:
            return self._dimension
        dummy = await self.embed("test")
        self._dimension = len(dummy)
        return self._dimension

    # ------------------------------------------------------------------
    # OpenAI 嵌入
    # ------------------------------------------------------------------

    async def _openai_embed_batch(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            embeddings = await self._call_openai_api(batch)
            results.extend(embeddings)
        return results

    async def _call_openai_api(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                _OPENAI_EMBED_URL,
                headers={"Authorization": f"Bearer {self._settings.openai_api_key}"},
                json={
                    "model": self._settings.rag_embedding_model,
                    "input": texts,
                },
            )
            response.raise_for_status()
            data = response.json()
        # data["data"] 按输入顺序排列
        return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]

    # ------------------------------------------------------------------
    # 本地 sentence-transformers 嵌入
    # ------------------------------------------------------------------

    async def _local_embed_batch(self, texts: list[str]) -> list[list[float]]:
        model = await self._ensure_local_model()
        loop = asyncio.get_event_loop()
        # encode 是 CPU 密集型，放到线程池避免阻塞事件循环
        embeddings = await loop.run_in_executor(None, lambda: model.encode(texts, normalize_embeddings=True))
        return [emb.tolist() for emb in embeddings]

    async def _ensure_local_model(self) -> object:
        if self._local_model is not None:
            return self._local_model

        loop = asyncio.get_event_loop()

        def _load():
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore
                return SentenceTransformer(self._settings.rag_local_embedding_model)
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers 未安装。请运行：pip install sentence-transformers"
                ) from exc

        self._local_model = await loop.run_in_executor(None, _load)
        return self._local_model
