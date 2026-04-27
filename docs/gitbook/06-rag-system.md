# 6. RAG 知识系统

这一轮之后，项目里的 RAG 不再只是“历史 EvidenceCard 缓存”。它被拆成两类知识来源：

- 外部知识库资料：通过 API 主动写入的业务资料、研究材料、用户上传文本或内部文档。
- 研究证据沉淀：任务完成后自动写入的 EvidenceCard，用于后续相似研究复用。

这更接近实际 RAG 知识系统：先有可维护的知识库，再让 Agent 在 Research 前检索知识库；如果知识库不足，再走 Web Search。

## 系统定位

RAG 在本项目中的定位是“Agent 的知识层”，不是项目 Markdown 文档问答。它解决三个问题：

- 让用户或系统可以主动写入外部知识资料。
- 让 Researcher 在联网搜索前优先检索知识库。
- 让已经完成的研究证据继续沉淀，形成可复用知识资产。

主链路仍然是：

```text
Planner -> Researcher -> Critic -> Writer
```

RAG 插在 Research 前后：

```text
Research 前：检索知识库和历史证据
Research 后：未命中时继续 Web Search
Write 后：把新 EvidenceCard 自动沉淀入库
```

## 外部知识入库

外部知识通过 API 写入：

```text
POST /rag/knowledge
```

请求体：

```json
{
  "source_id": "internal-agent-notes-001",
  "title": "内部 Agent 设计资料",
  "text": "这里放入业务资料、研究材料、用户上传文本或内部知识内容。",
  "source_url": "https://example.test/internal-agent-notes",
  "source_type": "internal_doc",
  "metadata": {
    "department": "research",
    "version": "2026-04"
  }
}
```

入库流程：

```text
KnowledgeIngestRequest
  -> SemanticChunker
  -> Embedder
  -> VectorDocument
  -> ChromaDB
```

向量文档 ID：

```text
knowledge:{source_id}:{chunk_idx}
```

## 知识检索

只检索主动写入的知识库资料：

```text
GET /rag/knowledge/search?q=<query>&top_k=5
```

这个接口会使用向量库过滤条件：

```text
record_type = knowledge
```

因此它不会混入任务自动沉淀的 EvidenceCard，适合验证“真实知识库”是否已经写入并可检索。

## 知识删除

删除某个知识源：

```text
DELETE /rag/knowledge/{source_id}
```

这会删除该 source_id 下的所有 chunk。

## 研究证据沉淀

任务完成后，系统仍会把 EvidenceCard 自动写入向量库：

```text
EvidenceCard
  -> claim + summary
  -> SemanticChunker
  -> Embedder
  -> VectorDocument
  -> ChromaDB
```

这类数据的 `record_type` 是：

```text
research_evidence
```

它和外部知识库资料共用底层向量库，但元数据不同。

## Research 前如何使用 RAG

Research 节点开始时会执行：

```text
pending_topics
  -> check_cache(topic.question)
  -> 命中足够知识/证据：转成 EvidenceCard
  -> 命中不足：进入 Web Search
```

这意味着 Agent 会优先利用已有知识系统，而不是每次都重新联网搜索。

## 元数据设计

外部知识资料保留：

```text
record_type = knowledge
source_id
title
source_url
source_type
chunk_idx
token_count
自定义 metadata
```

研究证据沉淀保留：

```text
record_type = research_evidence
task_id
card_id
topic_id
source_url
confidence
source_type
chunk_idx
token_count
```

## 展示口径

推荐这样描述：

> 项目实现了一个真实的 RAG 知识层：用户可以通过 API 写入外部知识资料，系统会进行语义分块、embedding 和 ChromaDB 持久化；Research 阶段会优先查询知识库和历史证据，命中后转成 EvidenceCard 参与 Critic 和 Writer 流程，未命中再走 Web Search。这样 RAG 不只是项目文档问答，而是 Agent 工作流中的知识基础设施。
