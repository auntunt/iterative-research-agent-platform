# 3. 多 Agent 编排

多 Agent 系统的难点不在于创建多个类，而在于控制它们什么时候运行、共享什么状态、失败后怎么恢复、如何避免重复工作。本项目把这些问题集中放在 `Orchestrator` 和 LangGraph 状态机里处理。

## 编排入口

核心类是 `app/services/orchestrator.py` 中的 `Orchestrator`。它负责：

- 初始化 LLMRouter。
- 初始化四类 Agent。
- 初始化可选 RAG 组件。
- 初始化 LangGraph Checkpointer。
- 构建任务状态图。
- 执行任务、恢复任务、记录日志和指标。

## LangGraph 状态图

项目使用 `StateGraph` 定义外层工作流：

```text
plan -> research -> critic -> write -> END
                   |
                   | evidence insufficient
                   v
                research

                   |
                   | max rounds reached or low confidence
                   v
              human_review -> write
```

状态图包含五个节点：

- `plan`：生成研究主题。
- `research`：并行执行检索和证据抽取。
- `critic`：评估证据充分性。
- `human_review`：人工审核断点。
- `write`：生成最终报告。

## 条件路由

Critic 完成后，系统调用 `_route_after_critic()` 决定下一步：

```text
assessment.sufficient == true
  -> write

assessment.sufficient == false 且仍有轮次
  -> research

超过最大轮次或没有 follow-up topic
  -> human_review
```

这就是项目里的迭代式研究闭环。系统不会默认相信第一轮检索结果，而是让 Critic 根据覆盖度和引用完整性决定是否继续查。

## 并行 Researcher

Planner 会生成 3-5 个 `ResearchTopic`。Research 节点内部使用 `asyncio.gather()` 并行执行多个研究主题：

```text
topic-1 -> ResearchAgent
topic-2 -> ResearchAgent
topic-3 -> ResearchAgent
...
```

并行执行的好处是：

- 缩短开放式研究的等待时间。
- 每个主题独立产生证据卡片。
- 状态合并时只需要按 `card_id` 去重。

这里没有为每个 topic 创建不同类型的 Agent，而是复用同一个 `ResearchAgent` 类，通过 `context["research_topic"]` 注入不同研究主题。

## Human-in-the-loop

项目支持人工审核断点。LangGraph 编译时设置：

```text
interrupt_before = ["human_review"]
```

当 Critic 认为证据不足且轮次耗尽时，任务可以停在人工审核节点。前端或 API 调用 `/task/{task_id}/resume` 后，系统会把审核结果写回 checkpoint，然后从断点继续执行。

这个设计适合展示“Agent 不是必须完全自动化”，而是可以在人类需要把关的地方暂停，让人确认证据质量。

## Agent 之间如何传递信息

Agent 之间不直接互相调用，而是通过图状态和 `context` 间接传递：

```text
Planner -> context["research_topics"]
Researcher -> context["evidence_cards"]
Critic -> context["critic_assessment"] + pending_topics
Writer -> 读取 research_topics/evidence_cards/critic_assessment
```

这样做有两个好处：

- Agent 之间解耦，单个 Agent 更容易测试。
- 所有中间状态都可以被 Orchestrator 记录和恢复。
