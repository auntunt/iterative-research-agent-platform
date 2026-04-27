# 2. Agent 开发设计

本项目的 Agent 设计采用统一基类加专用子类的方式。所有 Agent 都继承 `BaseAgent`，因此它们拥有一致的执行生命周期、模型调用入口、工具调用入口和返回结构。

## Agent 生命周期

`BaseAgent.run()` 定义了统一流程：

```text
plan -> act -> observe
```

含义如下：

- `plan`：确定当前 Agent 的目标、可用工具、成功标准或输入约束。
- `act`：真正执行任务，可能调用 LLM，也可能调用工具。
- `observe`：检查执行结果，例如是否有输出、工具是否成功、调用了几次 LLM。

这个设计比“直接写一个函数调用 LLM”更适合练手和工程展示，因为每个 Agent 的行为都被拆成可记录、可测试、可扩展的阶段。

## BaseAgent 的职责

`BaseAgent` 主要提供四类能力：

- 生命周期模板：统一 `run()` 方法。
- 工具管理：初始化时接收工具列表，并通过工具名调用。
- 模型调用：统一走 `LLMRouter.generate()`。
- 输出封装：返回 `AgentRun`，包含 Agent 输出、工具调用、模型使用和路由决策。

核心返回对象是 `AgentRun`：

```text
AgentRun
  output: AgentOutput
  llm_usage: list[LLMUsage]
  tool_calls: list[ToolResult]
  metadata: dict
  routing_decisions: list[RoutingDecision]
```

这让每次 Agent 执行都可以被写入数据库、展示到前端、导出到产物文件，或者用于后续审计。

## AgentOutput

每个 Agent 的主要输出都封装为 `AgentOutput`：

```text
thought  -> 本步骤的思考或执行摘要
action   -> 结构化动作名称
result   -> 面向用户或下游 Agent 的文本结果
metadata -> 给系统内部使用的结构化数据
```

例如 Researcher 的 `result` 是主题摘要，而 `metadata` 里会包含真正给后续节点使用的 `evidence_cards`。

## 为什么不用完全自由 ReAct

项目没有让 Agent 自由地无限循环“思考、工具、观察”，而是把 Agent 放进受控状态机。原因是：

- 研究报告任务需要稳定产物，而不是开放式聊天。
- 每个阶段的输入输出需要结构化，方便测试和展示。
- 工具调用和模型调用需要记录成本、延迟和失败原因。
- Critic 的反馈循环由 Orchestrator 控制，避免单个 Agent 自行失控。

因此，本项目是“工程约束下的 Agent”，不是“完全自治的 Agent”。这对练手项目反而更有价值，因为它展示的是如何把 Agent 生命周期、工具调用、结构化输出和状态编排组合起来，而不是只展示一次模型调用。

## 四类 Agent 的分工

| Agent | 核心职责 | 主要输出 |
| --- | --- | --- |
| PlannerAgent | 拆解开放问题，生成研究主题和任务图 | `ResearchTopic`、`TaskGraph` |
| ResearchAgent | 搜索、抓取、清洗、抽取证据 | `EvidenceCard` |
| CriticAgent | 判断证据是否足够，生成补充检索主题 | `CriticAssessment` |
| WritingAgent | 基于证据生成最终报告 | Markdown 报告 |

## Agent 扩展方式

新增一个 Agent 通常需要三步：

1. 继承 `BaseAgent`，实现自己的 `plan()` 和 `act()`。
2. 在 `Orchestrator.__init__()` 中注册 Agent。
3. 在 LangGraph 状态图中新增节点或条件边。

如果新 Agent 需要工具，则在初始化时传入工具实例：

```python
CustomAgent(llm_router, tools=[SomeTool(settings)])
```

如果新 Agent 只是做模型判断，则可以不传工具，直接使用 `_generate()`。
