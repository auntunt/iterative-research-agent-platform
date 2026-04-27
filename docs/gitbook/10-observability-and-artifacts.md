# 10. 可观测性与产物

Agent 系统要能展示和调试，必须记录每一步发生了什么。本项目从任务状态、步骤状态、日志、指标、模型路由、工具调用和导出产物几个层面做可观测性。

## 日志

系统使用结构化日志记录关键事件，例如：

```text
langgraph_node_completed
research_round_completed
critic_assessment_completed
human_review_filtered
human_review_approved
rag_ingestion_completed
task_failed
```

每条日志带有：

```text
task_id
trace_id
request_id
level
event
message
payload
created_at
```

这些信息可以通过 `/logs` API 查询。

## Metrics

任务指标聚合在 `TaskMetrics`：

```text
total_steps
successful_steps
failed_steps
retries
latency
success_rate
llm_calls
total_tokens
estimated_cost
avg_latency
provider_usage
```

这让项目可以回答：

- 本次任务用了几个 Agent 步骤？
- 总共调用了几次 LLM？
- 估算花费是多少？
- 哪些 Provider 被使用最多？
- 平均延迟是多少？

## 工具调用记录

每个 Step 都会保存 `tool_calls`。Researcher 的步骤里通常可以看到：

```text
web_search
web_fetch
web_fetch
...
```

这对调试很重要，因为研究结果差时可以判断是搜索源问题、抓取问题、页面清洗问题，还是 LLM 抽取问题。

## 模型路由日志

`RoutingDecision` 记录每次模型选择：

```text
agent
provider
model
tier
complexity_score
reason
estimated_cost_usd
fallback_used
```

对应 API 是：

```text
GET /task/{task_id}/routing_log
```

这适合展示“不是所有地方都盲目用大模型”，而是按角色和复杂度选择。

## 上下文统计

上下文统计 API：

```text
GET /task/{task_id}/context_stats
```

返回：

```text
rolling_summary
compression_count
tokens_saved
```

它用于展示长任务里如何管理上下文窗口。

## 产物导出

导出接口：

```text
POST /task/{task_id}/artifacts
```

导出内容包括：

- `report.md`：最终报告。
- `evidence_cards.json`：证据卡片。
- `citations.json`：引用索引。
- `trace.json`：执行轨迹。
- `metrics.json`：指标。
- `run_manifest.json`：运行清单。

这些产物可以用于演示，但建议只保留精选样例，不要把大量运行结果提交到仓库。
