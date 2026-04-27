from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.logger import get_logger, log_context
from app.rag.vector_store import VectorDocument

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.models.schemas import EvidenceCard
    from app.rag.embedder import Embedder
    from app.rag.semantic_chunker import SemanticChunker
    from app.rag.vector_store import SearchResult, VectorStore

logger = get_logger(__name__)


@dataclass
class IngestionResult:
    task_id: str
    card_count: int
    chunks_written: int
    chunks_skipped: int
    duration_ms: float
    error: str | None = None


@dataclass
class KnowledgeIngestionResult:
    source_id: str
    title: str
    chunks_written: int
    chunks_skipped: int
    duration_ms: float
    error: str | None = None


class AutoIngester:
    """
    任务完成后将证据卡片自动向量化入库，并提供知识库预查询接口。

    入库时机：orchestrator._graph_write 完成后异步触发，不阻塞响应。
    查询时机：ResearchAgent 开始前调用 check_cache，命中则跳过网络搜索。
    """

    def __init__(
        self,
        vector_store: VectorStore,
        embedder: Embedder,
        chunker: SemanticChunker,
        settings: Settings,
    ) -> None:
        self.vector_store = vector_store
        self.embedder = embedder
        self.chunker = chunker
        self.settings = settings
        self._ingestion_log: list[IngestionResult] = []
        self._knowledge_ingestion_log: list[KnowledgeIngestionResult] = []

    async def ingest_task(
        self,
        task_id: str,
        evidence_cards: list[EvidenceCard],
    ) -> IngestionResult:
        """
        将 evidence_cards 语义分块、嵌入后幂等写入向量数据库。
        幂等保证：VectorDocument.id = f"{task_id}:{card_id}:{chunk_idx}"，
        相同 id 覆盖写入。
        """
        started = time.perf_counter()
        docs: list[VectorDocument] = []
        skipped = 0

        for card in evidence_cards:
            combined_text = f"{card.claim}\n{card.summary}"
            try:
                chunks = await self.chunker.chunk(combined_text)
            except Exception as exc:
                logger.warning("chunker_failed", extra={"card_id": card.card_id, "error": str(exc)})
                skipped += 1
                continue

            texts = [c.text for c in chunks]
            try:
                embeddings = await self.embedder.embed_batch(texts)
            except Exception as exc:
                logger.warning("embed_failed", extra={"card_id": card.card_id, "error": str(exc)})
                skipped += len(chunks)
                continue

            for chunk, embedding in zip(chunks, embeddings):
                doc_id = f"{task_id}:{card.card_id}:{chunk.idx}"
                docs.append(
                    VectorDocument(
                        id=doc_id,
                        embedding=embedding,
                        text=chunk.text,
                        metadata={
                            "task_id": task_id,
                            "record_type": "research_evidence",
                            "card_id": card.card_id,
                            "topic_id": card.topic_id,
                            "source_url": card.citation.url,
                            "confidence": card.confidence,
                            "source_type": card.source_type,
                            "chunk_idx": chunk.idx,
                            "token_count": chunk.token_count,
                        },
                    )
                )

        if docs:
            try:
                await self.vector_store.upsert_batch(docs)
            except Exception as exc:
                duration_ms = (time.perf_counter() - started) * 1000
                result = IngestionResult(
                    task_id=task_id,
                    card_count=len(evidence_cards),
                    chunks_written=0,
                    chunks_skipped=skipped + len(docs),
                    duration_ms=duration_ms,
                    error=str(exc),
                )
                self._ingestion_log.append(result)
                logger.error("ingest_failed", extra={"task_id": task_id, "error": str(exc)})
                return result

        duration_ms = (time.perf_counter() - started) * 1000
        result = IngestionResult(
            task_id=task_id,
            card_count=len(evidence_cards),
            chunks_written=len(docs),
            chunks_skipped=skipped,
            duration_ms=round(duration_ms, 2),
        )
        self._ingestion_log.append(result)
        logger.info(
            "ingest_completed",
            extra={
                "task_id": task_id,
                "chunks_written": len(docs),
                "duration_ms": duration_ms,
            },
        )
        return result

    async def ingest_knowledge(
        self,
        source_id: str,
        title: str,
        text: str,
        source_url: str = "",
        source_type: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeIngestionResult:
        """
        将外部知识资料写入向量库。

        这条链路面向业务资料、研究材料、用户上传内容等真实知识源，
        与任务完成后自动沉淀的 EvidenceCard 区分存储。
        """
        started = time.perf_counter()
        metadata = metadata or {}
        docs: list[VectorDocument] = []
        skipped = 0

        try:
            chunks = await self.chunker.chunk(text)
        except Exception as exc:
            result = KnowledgeIngestionResult(
                source_id=source_id,
                title=title,
                chunks_written=0,
                chunks_skipped=1,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                error=str(exc),
            )
            self._knowledge_ingestion_log.append(result)
            logger.warning("knowledge_chunker_failed", extra={"source_id": source_id, "error": str(exc)})
            return result

        try:
            embeddings = await self.embedder.embed_batch([chunk.text for chunk in chunks])
        except Exception as exc:
            result = KnowledgeIngestionResult(
                source_id=source_id,
                title=title,
                chunks_written=0,
                chunks_skipped=len(chunks),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                error=str(exc),
            )
            self._knowledge_ingestion_log.append(result)
            logger.warning("knowledge_embed_failed", extra={"source_id": source_id, "error": str(exc)})
            return result

        safe_extra = self._safe_metadata(metadata)
        for chunk, embedding in zip(chunks, embeddings):
            docs.append(
                VectorDocument(
                    id=f"knowledge:{source_id}:{chunk.idx}",
                    embedding=embedding,
                    text=chunk.text,
                    metadata={
                        **safe_extra,
                        "record_type": "knowledge",
                        "source_id": source_id,
                        "title": title,
                        "source_url": source_url,
                        "source_type": source_type,
                        "chunk_idx": chunk.idx,
                        "token_count": chunk.token_count,
                    },
                )
            )

        if docs:
            try:
                await self.vector_store.upsert_batch(docs)
            except Exception as exc:
                skipped = len(docs)
                docs = []
                logger.error("knowledge_ingest_failed", extra={"source_id": source_id, "error": str(exc)})
                error = str(exc)
            else:
                error = None
        else:
            error = None

        result = KnowledgeIngestionResult(
            source_id=source_id,
            title=title,
            chunks_written=len(docs),
            chunks_skipped=skipped,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            error=error,
        )
        self._knowledge_ingestion_log.append(result)
        logger.info(
            "knowledge_ingest_completed",
            extra={"source_id": source_id, "chunks_written": result.chunks_written, "duration_ms": result.duration_ms},
        )
        return result

    async def check_cache(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """
        在向量数据库中查找与 query 语义相似的已有知识。
        返回相似度 >= settings.rag_min_cache_score 的结果。
        """
        try:
            embedding = await self.embedder.embed(query)
            return await self.vector_store.search(
                embedding,
                top_k=top_k,
                min_score=self.settings.rag_min_cache_score,
            )
        except Exception as exc:
            logger.warning("cache_check_failed", extra=log_context(query=query[:100], error=str(exc)))
            return []

    async def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """手动查询接口（供 API 层调用）。"""
        try:
            embedding = await self.embedder.embed(query)
            return await self.vector_store.search(embedding, top_k=top_k, min_score=0.0)
        except Exception as exc:
            logger.warning("rag_search_failed", extra={"error": str(exc)})
            return []

    async def search_knowledge(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """只检索主动写入的知识库资料，不混入任务自动沉淀的证据卡片。"""
        try:
            embedding = await self.embedder.embed(query)
            return await self.vector_store.search(
                embedding,
                top_k=top_k,
                min_score=0.0,
                where={"record_type": "knowledge"},
            )
        except Exception as exc:
            logger.warning("knowledge_search_failed", extra=log_context(query=query[:100], error=str(exc)))
            return []

    async def delete_knowledge_source(self, source_id: str) -> int:
        return await self.vector_store.delete_by_source(source_id)

    def ingestion_history(self) -> list[dict[str, Any]]:
        return [
            {
                "task_id": r.task_id,
                "card_count": r.card_count,
                "chunks_written": r.chunks_written,
                "chunks_skipped": r.chunks_skipped,
                "duration_ms": r.duration_ms,
                "error": r.error,
            }
            for r in self._ingestion_log
        ]

    def knowledge_ingestion_history(self) -> list[dict[str, Any]]:
        return [
            {
                "source_id": r.source_id,
                "title": r.title,
                "chunks_written": r.chunks_written,
                "chunks_skipped": r.chunks_skipped,
                "duration_ms": r.duration_ms,
                "error": r.error,
            }
            for r in self._knowledge_ingestion_log
        ]

    @staticmethod
    def _safe_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
        safe: dict[str, str | int | float | bool] = {}
        for key, value in metadata.items():
            if isinstance(value, (str, int, float, bool)):
                safe[str(key)] = value
            elif value is not None:
                safe[str(key)] = str(value)
        return safe
