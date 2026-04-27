# 8. 模型路由

项目没有把所有 Agent 都固定到同一个模型，而是使用 `LLMRouter` 根据任务复杂度、Agent 类型、Token 数、Provider 配置和失败情况选择模型。

## 路由目标

模型路由解决三个问题：

- 成本控制：简单任务和摘要任务不必使用大模型。
- 能力匹配：Planner、Critic 等关键判断环节可以使用更强模型。
- 可用性：Provider 失败时自动 fallback 到其他 Provider 或 local mock。

## Provider 列表

系统支持以下 Provider：

```text
local
openai
openai_nano
openai_mini
anthropic
openrouter
qwen
```

没有 API key 或远程服务不可用时，Provider 会退回 local mock，保证项目可以本地演示。

## LLMContext

每次模型调用都会构造 `LLMContext`：

```text
task
agent
complexity
excluded_providers
metadata
```

这让路由器知道当前是谁在调用模型、任务复杂度如何、Prompt 大概多长、哪些 Provider 已经失败或被黑名单排除。

## 选择规则

优先级大致如下：

1. 如果配置了 `agent_provider_overrides`，优先使用该 Agent 的指定 Provider。
2. 如果 Prompt token 超过复杂任务阈值，使用复杂任务 Provider。
3. 如果任务复杂度是 `complex`，使用复杂任务 Provider。
4. 如果 Agent 是 Planner 或 Critic 且不是简单任务，使用复杂任务 Provider。
5. 否则使用简单任务 Provider。

之后再加上 fallback providers、默认 Provider 和 local 兜底。

## RoutingDecision

每次 LLM 调用都会生成 `RoutingDecision`：

```text
agent
provider
model
tier
complexity_score
signals
reason
estimated_cost_usd
fallback_used
```

这些信息可以用于前端展示，也可以用于复盘模型选择是否合理。

## 失败与降级

如果某个 Provider 调用失败，系统会：

1. 记录失败次数。
2. 把该 Provider 加入当前调用的排除集合。
3. 尝试下一个候选 Provider。
4. 所有候选都失败时，使用 local。

这使得模型调用不是单点故障。即使 OpenAI、OpenRouter 或本地 vLLM 不可用，系统仍可以跑通流程，适合作为练手项目和演示项目。

## 适合展示的说法

> 我在项目里做了一个轻量模型路由器。每个 Agent 调用模型时都会携带 LLMContext，路由器根据 Agent 角色、任务复杂度、Prompt token、Provider 黑名单和历史成功率选择 Provider。调用结果会记录 token、延迟、成本和 fallback 状态，最后统一写入 StepState 和 routing log。
