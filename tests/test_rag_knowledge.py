from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.rag.auto_ingester import AutoIngester
from app.rag.vector_store import SearchResult


class FakeEmbedder:
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(index)] for index, _ in enumerate(texts)]

    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


class FakeChunker:
    async def chunk(self, text: str):
        return [
            SimpleNamespace(idx=0, text=text[:40], token_count=10),
            SimpleNamespace(idx=1, text=text[40:] or text, token_count=8),
        ]


class FakeVectorStore:
    def __init__(self) -> None:
        self.docs = []
        self.last_search_where = None
        self.deleted_source_id = ""

    async def upsert_batch(self, docs):
        self.docs.extend(docs)

    async def search(self, embedding, top_k=5, min_score=0.0, where=None):
        self.last_search_where = where
        return [
            SearchResult(
                doc_id="knowledge:source-1:0",
                text="内部知识库说明了多智能体系统的检索策略。",
                score=0.91,
                metadata={"record_type": "knowledge", "source_id": "source-1", "title": "内部资料"},
            )
        ]

    async def delete_by_source(self, source_id: str) -> int:
        self.deleted_source_id = source_id
        return 2


@pytest.mark.asyncio
async def test_ingest_knowledge_writes_external_knowledge_documents() -> None:
    store = FakeVectorStore()
    ingester = AutoIngester(store, FakeEmbedder(), FakeChunker(), Settings())

    result = await ingester.ingest_knowledge(
        source_id="source-1",
        title="内部资料",
        text="这是一份用于 RAG 知识系统的外部业务资料，用来验证不是项目 Markdown 文档。",
        source_url="https://example.test/internal",
        source_type="internal_doc",
        metadata={"department": "research", "nested": {"ignored": "as-string"}},
    )

    assert result.source_id == "source-1"
    assert result.chunks_written == 2
    assert len(store.docs) == 2
    assert store.docs[0].id == "knowledge:source-1:0"
    assert store.docs[0].metadata["record_type"] == "knowledge"
    assert store.docs[0].metadata["source_type"] == "internal_doc"
    assert store.docs[0].metadata["department"] == "research"
    assert isinstance(store.docs[0].metadata["nested"], str)


@pytest.mark.asyncio
async def test_search_knowledge_filters_to_knowledge_records() -> None:
    store = FakeVectorStore()
    ingester = AutoIngester(store, FakeEmbedder(), FakeChunker(), Settings())

    results = await ingester.search_knowledge("多智能体检索策略")

    assert len(results) == 1
    assert store.last_search_where == {"record_type": "knowledge"}


@pytest.mark.asyncio
async def test_delete_knowledge_source_deletes_by_source_id() -> None:
    store = FakeVectorStore()
    ingester = AutoIngester(store, FakeEmbedder(), FakeChunker(), Settings())

    deleted = await ingester.delete_knowledge_source("source-1")

    assert deleted == 2
    assert store.deleted_source_id == "source-1"
