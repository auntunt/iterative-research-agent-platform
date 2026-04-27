# 7. 记忆系统与上下文血统

这里的“记忆”不是单一数据库表，而是几类不同用途的状态沉淀。项目把短期事件、长期 KV、滚动摘要、上下文压缩和证据窗口分开处理。

## 为什么要分层记忆

Agent 系统里的记忆至少有四种需求：

- 运行时事件：某个任务发生了什么。
- 长期总结：任务完成后留下什么结论。
- 上下文压缩：Prompt 太长时如何保留关键信息。
- 证据血统：最终结论来自哪些来源和段落。

如果把这些都混在一个“memory”字段里，后续调试和展示会很困难。所以项目采用分层设计。

## MemoryStore

`MemoryStore` 提供两类存储：

```text
_events: dict[task_id, list[event]]
_kv: dict[key, value]
```

`_events` 用于保存任务内事件，例如：

```text
agent_step_success
evidence_card_created
critic_assessment_completed
task_failed
rag_ingestion_completed
```

`_kv` 用于保存长期信息，例如：

```text
task:{task_id}:summary
rolling_summary:{task_id}
compression_stats:{task_id}
```

长期 KV 会写入 `data/long_term_memory.json`。这个文件是运行产物，不应该提交到 GitHub。

## 滚动摘要

每个 Agent 步骤完成后，Orchestrator 会异步调用 `IncrementalSummarizer`：

```text
AgentRun
  -> summarize_agent_run
  -> rolling_update
  -> MemoryStore.set_rolling_summary
```

滚动摘要的目标是保留任务过程中的关键发现，而不是把每个工具返回的长文本都塞进后续 Prompt。

## 上下文压缩

`ContextWindowManager` 用于控制上下文长度。它有两种压缩方式：

- `fit_context()`：单段上下文超预算时压缩成摘要。
- `fit_messages()`：多轮消息超预算时保留最新消息，把溢出的旧消息压缩成 system 摘要。

核心思想是：新信息优先保留，旧信息压缩保留。

## 证据窗口

Orchestrator 里还有一个 `evidence_window`：

```text
context["evidence_window"] = last N evidence cards
```

这不是长期记忆，而是给当前 Agent 使用的滑动证据窗口。它避免把所有历史证据都塞给模型，同时保留最近最相关的证据。

## 记忆血统

本项目里的“血统”可以理解为每条结论的来源链：

```text
最终报告段落
  -> 引用编号
  -> EvidenceCard
  -> Citation
  -> URL + title + paragraph_id + snippet
  -> ResearchTopic
  -> Planner 拆解的问题
```

这条链路让系统可以说明：报告里的结论不是凭空生成，而是来自某个研究主题下的某张证据卡，证据卡又来自某个网页的具体段落。

## 记忆与 RAG 的区别

| 模块 | 作用 | 数据形态 |
| --- | --- | --- |
| MemoryStore events | 记录任务过程 | 事件列表 |
| MemoryStore KV | 保存长期摘要和统计 | JSON KV |
| Rolling Summary | 压缩过程信息 | 文本摘要 |
| Evidence Window | 控制当前 Prompt 证据上下文 | 最近 N 张证据卡 |
| RAG Vector Store | 复用历史证据 | 向量文档 |

这几类模块共同构成项目的上下文管理体系。
