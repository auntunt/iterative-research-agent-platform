# 6. RAG 系统

本项目的 RAG 不是作为主流程的唯一知识来源，而是作为“研究结果复用层”。系统第一次完成研究后，把证据卡片写入向量库；后续遇到相似主题时，Research 节点会先查 RAG 缓存，命中足够证据后可以跳过网络搜索。

## RAG 的定位

RAG 在项目中承担三个目标：

- 复用历史研究成果，减少重复搜索。
- 提高相似任务的响应速度。
- 把已验证的证据卡片沉淀为长期知识资产。

它不是替代 Web Search，而是放在 Web Search 前面作为缓存层。

## 写入时机

RAG 入库发生在 `write` 节点之后。也就是说，只有已经走完 Planner、Researcher、Critic、Writer 流程的任务，才会把证据写入知识库。

这样做的好处是：进入 RAG 的内容不是随便抓来的网页片段，而是已经被 Critic 流程审查过、被 Writer 使用过的证据卡片。

## 查询时机

Research 节点开始时会先调用 `_split_topics_by_cache()`：

```text
pending_topics
  -> check_cache(topic.question)
  -> RAG 命中足够证据：直接转 EvidenceCard
  -> RAG 不足：进入 Web Search
```

这形成了一个缓存优先的研究链路：

```text
RAG cache hit -> use cached evidence
RAG cache miss -> search web -> fetch -> extract evidence
```

## 入库流程

`AutoIngester` 负责自动入库：

```text
EvidenceCard
  -> claim + summary
  -> SemanticChunker
  -> Embedder
  -> VectorDocument
  -> VectorStore.upsert_batch
```

每个向量文档的 ID 使用：

```text
task_id:card_id:chunk_idx
```

这保证同一个任务、同一张证据卡、同一个 chunk 可以幂等写入。

## 元数据设计

向量库中每个文档保留以下元数据：

```text
task_id
card_id
topic_id
source_url
confidence
source_type
chunk_idx
token_count
```

这些字段使 RAG 结果可以重新转换成 `EvidenceCard`，并保留来源 URL 和主题归属。

## 与普通 RAG 的区别

很多 RAG 项目是“文档入库 -> 用户提问 -> 检索片段 -> 生成答案”。本项目是“研究流程产生证据 -> 证据入库 -> 后续研究复用证据”。

区别在于：

- 入库对象不是原始文档，而是证据卡片。
- 检索结果不是直接塞进 Prompt，而是还原成 EvidenceCard。
- RAG 是多 Agent 流程的一部分，而不是单独问答链。

## 展示价值

在 GitHub 或面试中，可以这样解释：

> 我没有把 RAG 只做成一个简单的向量检索问答，而是把它作为 Agent 工作流的知识复用层。任务完成后，系统把经过证据抽取和 Critic 审查的 EvidenceCard 异步写入向量库；后续研究开始前先查缓存，命中足够证据就跳过网络搜索，未命中再进入 Web Search 工具链。
