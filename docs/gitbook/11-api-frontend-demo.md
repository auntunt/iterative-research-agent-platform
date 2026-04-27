# 11. API、前端与演示

项目提供 FastAPI 接口和轻量中文前端。前端主要用于演示任务提交、任务列表、执行时间线、证据卡片、Critic 评分、报告结果和平台指标。

## API 入口

核心接口如下：

| 接口 | 作用 |
| --- | --- |
| `POST /run_task` | 提交研究任务 |
| `GET /task/{task_id}` | 查询任务状态和结果 |
| `DELETE /task/{task_id}` | 删除任务 |
| `GET /task/{task_id}/evidence` | 查询证据卡片和 Critic 评估 |
| `POST /task/{task_id}/artifacts` | 导出任务产物 |
| `GET /tasks` | 分页查询任务 |
| `GET /metrics` | 查询平台指标 |
| `GET /logs` | 查询日志 |
| `GET /task/{task_id}/routing_log` | 查询模型路由记录 |
| `GET /task/{task_id}/context_stats` | 查询上下文统计 |
| `POST /task/{task_id}/resume` | Human-in-the-loop 恢复 |
| `GET /rag/stats` | 查询 RAG 知识库统计 |
| `POST /rag/knowledge` | 写入外部知识资料 |
| `GET /rag/knowledge/search` | 只检索主动写入的知识库资料 |
| `DELETE /rag/knowledge/{source_id}` | 删除某个知识源 |
| `GET /rag/search` | 手动查询 RAG |

## 前端工作台

前端位于 `frontend/`，由 FastAPI 静态挂载：

```text
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
```

主要能力：

- 输入中文研究任务。
- 查看任务列表和状态。
- 查看任务详情。
- 展示 Agent 时间线。
- 展示证据卡片。
- 展示 Critic 评估。
- 导出证据 JSON 或 Markdown。
- 查看平台指标。

## 本地运行

```bash
cd multi-agent-task-platform
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

打开：

```text
http://127.0.0.1:8000
```

## 演示任务建议

适合展示的任务应该是开放式、有多个角度、需要证据支撑的问题，例如：

```text
研究迭代式多智能体系统如何缓解复杂 RAG 流程中的证据覆盖盲点
```

这类任务能触发 Planner 拆解、Researcher 并行检索、Critic 检查和 Writer 写报告，展示效果比简单问答更好。

## 演示顺序

推荐演示顺序：

1. 打开前端，提交研究任务。
2. 观察任务列表变为 running。
3. 展示 Agent 时间线。
4. 展示证据卡片和来源链接。
5. 展示 Critic 评分。
6. 展示最终报告。
7. 调用 artifacts 导出产物。
8. 打开 routing log，解释模型路由。
9. 打开 context stats，解释滚动摘要和上下文管理。

## 注意事项

如果没有配置真实 API key，系统会退回 local mock，可以跑通流程，但报告质量和搜索效果会受限。用于 GitHub 展示时，这反而是优点：别人 clone 后不配置 key 也能看到系统架构和基本流程。
