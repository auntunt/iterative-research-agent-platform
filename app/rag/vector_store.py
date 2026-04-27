from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.config import Settings

_COLLECTION_NAME = "research_evidence"


@dataclass
class VectorDocument:
    id: str
    embedding: list[float]
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    doc_id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore:
    """
    基于 ChromaDB 的向量数据库封装。

    - 本地持久化至 settings.rag_chroma_persist_dir
    - 所有操作幂等（upsert 覆盖已有 id）
    - 异步接口，内部用线程池执行 chromadb 同步操作
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None
        self._collection: Any = None
        self._lock = asyncio.Lock()

    async def _ensure_collection(self) -> Any:
        if self._collection is not None:
            return self._collection

        async with self._lock:
            if self._collection is not None:
                return self._collection

            loop = asyncio.get_event_loop()
            client, collection = await loop.run_in_executor(None, self._init_chroma)
            self._client = client
            self._collection = collection

        return self._collection

    def _init_chroma(self) -> tuple[Any, Any]:
        try:
            import chromadb  # type: ignore
        except ImportError as exc:
            raise RuntimeError("chromadb 未安装。请运行：pip install chromadb") from exc

        client = chromadb.PersistentClient(path=self._settings.rag_chroma_persist_dir)
        collection = client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        return client, collection

    async def upsert(self, doc: VectorDocument) -> None:
        await self.upsert_batch([doc])

    async def upsert_batch(self, docs: list[VectorDocument]) -> None:
        if not docs:
            return
        collection = await self._ensure_collection()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: collection.upsert(
                ids=[d.id for d in docs],
                embeddings=[d.embedding for d in docs],
                documents=[d.text for d in docs],
                metadatas=[d.metadata for d in docs],
            ),
        )

    async def search(
        self,
        embedding: list[float],
        top_k: int = 5,
        min_score: float = 0.70,
        where: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        collection = await self._ensure_collection()
        loop = asyncio.get_event_loop()

        def _query() -> dict[str, Any]:
            kwargs: dict[str, Any] = {
                "query_embeddings": [embedding],
                "n_results": min(top_k, max(1, await_count())),
                "include": ["documents", "metadatas", "distances"],
            }
            if where:
                kwargs["where"] = where
            return collection.query(**kwargs)

        def await_count() -> int:
            return collection.count()

        raw = await loop.run_in_executor(None, _query)

        results: list[SearchResult] = []
        ids = (raw.get("ids") or [[]])[0]
        docs = (raw.get("documents") or [[]])[0]
        metas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]

        for doc_id, text, meta, dist in zip(ids, docs, metas, distances):
            # chromadb cosine distance → similarity = 1 - distance
            score = round(1.0 - float(dist), 4)
            if score >= min_score:
                results.append(SearchResult(doc_id=doc_id, text=text, score=score, metadata=meta or {}))

        return sorted(results, key=lambda r: r.score, reverse=True)

    async def delete_by_task(self, task_id: str) -> int:
        return await self.delete_where({"task_id": task_id})

    async def delete_by_source(self, source_id: str) -> int:
        return await self.delete_where({"source_id": source_id})

    async def delete_where(self, where: dict[str, Any]) -> int:
        collection = await self._ensure_collection()
        loop = asyncio.get_event_loop()

        def _delete() -> int:
            existing = collection.get(where=where)
            ids = existing.get("ids", [])
            if ids:
                collection.delete(ids=ids)
            return len(ids)

        return await loop.run_in_executor(None, _delete)

    async def count(self, where: dict[str, Any] | None = None) -> int:
        collection = await self._ensure_collection()
        loop = asyncio.get_event_loop()
        if not where:
            return await loop.run_in_executor(None, collection.count)

        def _count_where() -> int:
            existing = collection.get(where=where)
            return len(existing.get("ids", []))

        return await loop.run_in_executor(None, _count_where)

    async def stats(self) -> dict[str, Any]:
        total = await self.count()
        knowledge_total = await self.count({"record_type": "knowledge"})
        evidence_total = await self.count({"record_type": "research_evidence"})
        return {
            "collection": _COLLECTION_NAME,
            "total_documents": total,
            "knowledge_documents": knowledge_total,
            "research_evidence_documents": evidence_total,
            "persist_dir": self._settings.rag_chroma_persist_dir,
        }
