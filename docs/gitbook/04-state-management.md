# 4. 状态管理

Agent 系统如果没有状态管理，很快会变成一串不可复盘的 LLM 调用。本项目把任务、步骤、图状态、日志、指标和 checkpoint 分层管理。

## 状态层次

系统里主要有四类状态：

- `TaskState`：一次用户任务的整体状态。
- `StepState`：某个 Agent 步骤的执行状态。
- `ResearchGraphState`：LangGraph 运行中的工作流状态。
- `MemoryStore`：任务事件、长期记忆和滚动摘要。

## TaskState

`TaskState` 是对外最重要的任务模型，包含：

```text
task_id
trace_id
request_id
task
status
steps
result
error
metadata
metrics
task_graph
created_at
updated_at
```

它解决的是“一个任务现在运行到哪里了”的问题。

## StepState

每个 Agent 执行都会创建一个 `StepState`，包含：

```text
step_id
agent
input
output
status
llm_provider
llm_model
latency
retries
error
tool_calls
llm_usage
depends_on
```

这让系统可以回答：

- 哪个 Agent 失败了？
- 调用了什么工具？
- 用了哪个模型？
- 花了多少 token 和成本？
- 是否发生重试？

## ResearchGraphState

LangGraph 内部状态用于控制研究流程：

```text
task_id
task_state
task
context
topics
pending_topics
evidence_cards
assessment
round_index
max_research_rounds
final_output
rag_cache_hits
routing_decisions
human_approved
```

这个状态不是简单的字符串上下文，而是结构化的工作流数据。每个节点只更新自己负责的字段。

## Checkpoint

项目支持 LangGraph Checkpointer。初始化时先使用内存 checkpoint，首次执行时尝试升级为 SQLite checkpoint。

Checkpoint 的作用是：

- 支持 human-in-the-loop 暂停和恢复。
- 节点完成后保存中间状态。
- 服务重启后仍有机会恢复任务状态。

对应数据默认写入 `data/checkpoints.db`，这个文件属于运行产物，不应该上传 GitHub。

## 状态更新原则

项目里的状态更新遵循几个原则：

- API 只负责创建任务和查询结果，不直接执行长任务。
- Queue 负责把任务交给 Orchestrator，避免阻塞请求。
- Orchestrator 是唯一的编排中心，负责调用 Agent 和更新步骤。
- Agent 只产出 `AgentRun`，不直接操作数据库。
- TaskManager 负责持久化任务、步骤、日志和指标。

这种分层让代码边界比较清楚：Agent 专注智能行为，Orchestrator 专注流程，TaskManager 专注持久化。
