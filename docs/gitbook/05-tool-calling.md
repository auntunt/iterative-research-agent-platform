# 5. 工具调用

本项目里的工具层是 Agent 和外部世界之间的边界。Researcher 不直接写搜索和抓取逻辑，而是通过工具调用完成搜索、抓取、文档读取等动作。

## 工具抽象

工具统一返回 `ToolResult`：

```text
tool
success
output
latency
metadata
```

这个结构使工具调用可以被统一记录到 `StepState.tool_calls`，前端和导出产物都能看到工具调用历史。

## 当前工具

| 工具 | 作用 |
| --- | --- |
| `WebSearchTool` | 搜索网页，返回候选 URL、标题、摘要和分数 |
| `WebFetchTool` | 抓取 URL 正文，抽取段落 |
| `DocumentReaderTool` | 读取本地文档作为研究来源 |
| `MarkdownRenderTool` | 检查 Writer 生成的 Markdown 是否可渲染 |
| `PythonExecTool` | 预留给受控 Python 执行场景 |

## Researcher 的工具链

Researcher 的典型链路是：

```text
query
  -> web_search
  -> search results
  -> web_fetch for each URL
  -> paragraphs
  -> EvidenceCard
```

每个研究主题最多取若干 query，每个 query 最多取若干来源。这样可以通过配置控制搜索广度，避免一次任务抓取过多页面。

## Web Search

搜索工具支持多种 provider：

- Tavily：适合真实搜索 API。
- SearXNG：适合自托管搜索。
- Playwright：适合浏览器兜底搜索。
- DuckDuckGo：作为轻量兜底。

推荐展示时强调 `auto` 模式：系统优先使用稳定来源，失败时逐级 fallback。

## Web Fetch

抓取工具优先使用 Crawl4AI 做正文抽取和 Markdown 清洗；如果不可用，则退回到轻量 HTML 解析。

抓取结果会被切成段落，每段带有：

```text
paragraph_id
text
```

这个段落 ID 对证据追踪非常关键，因为最终报告中的引用不是只指向网页，而是指向网页中的具体段落。

## 工具失败处理

工具失败不会总是直接让整个任务失败。比如单个网页抓取失败时，Researcher 会退回使用搜索结果里的摘要信息，继续处理其他来源。

但如果 `AgentRun.tool_calls` 中存在失败工具，Orchestrator 会在步骤层面识别并触发重试逻辑。这样可以兼顾鲁棒性和可观测性。

## 与 MCP 的关系

项目里的工具风格接近 MCP 工具的工程抽象：每个工具有明确输入、输出、成功状态和元数据。虽然当前实现是本地 Python 工具类，但迁移到 MCP Server 时，边界是清楚的：

- `BaseTool` 对应工具协议层。
- `ToolResult` 对应工具调用返回。
- Agent 只依赖工具名和结构化输出，不依赖具体实现。
