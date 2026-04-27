# 迭代式深度研究智能体平台：工程设计手册

这份 GitBook 文档用于讲清楚本项目的 Agent 工程设计，而不是演讲稿脚本。它面向复习、面试展示和开源仓库阅读，重点解释系统为什么这样拆、每层如何协作、状态如何流转、证据如何追踪，以及 RAG、记忆、模型路由和工具调用在真实工程中的位置。

这个项目的定位是练手和复习型项目，不刻意包装成成熟商业产品。它的价值在于把 Agent 开发中常见的关键模块放到一条完整链路里：用户提交一个开放式研究问题后，系统会把问题拆成多个研究主题，并行检索网页，抽取段落级证据卡片，再由 Critic 判断证据是否足够。如果证据不足，流程会追加检索；如果证据可用，Writer 会基于证据卡片生成带引用的中文 Markdown 报告。

## 项目定位

本项目比较适合用一句话概括：

> 一个用于练习和复盘 Agent 工程能力的多智能体研究平台，重点不是追求单点功能极致，而是把规划、工具调用、RAG、记忆、状态管理、模型路由、证据追踪和可观测性串成一套能跑通、能讲清、能继续扩展的系统。

因此，阅读这份文档时要抓住两个重点：

- 它不是生产级系统，仍然有很多可以继续加强的地方，比如权限隔离、评测体系、前端体验、异常场景覆盖和真实搜索质量。
- 它不是单文件玩具 Demo，而是一个刻意把 Agent 工程关键概念拆开实现、再组合起来的练习项目。

## 适合展示的关键词

- Agent 生命周期：`plan -> act -> observe`
- LangGraph 状态机：`plan -> research -> critic -> write`
- 多智能体协作：Planner、Researcher、Critic、Writer
- 工具调用：Web Search、Web Fetch、Document Reader、Markdown Render
- RAG 缓存：任务完成后入库，后续研究前预查询
- 记忆系统：事件记忆、长期 KV 记忆、滚动摘要、上下文压缩
- 证据追踪：EvidenceCard、Citation、段落 ID、引用索引
- 模型路由：按复杂度、Agent 类型、Token 数和 Provider 健康度选择模型
- 状态管理：TaskState、StepState、TaskGraph、Checkpoint、Human-in-the-loop
- 可观测性：日志、Trace、Metrics、Artifacts、Routing Log
- 项目定位：练手、复习、展示技术整合能力，而不是过度包装成熟度

## 快速阅读路径

如果只想快速理解项目，推荐按下面顺序阅读：

1. [系统总览](./01-system-overview.md)
2. [Agent 开发设计](./02-agent-development-design.md)
3. [多 Agent 编排](./03-multi-agent-orchestration.md)
4. [证据追踪](./09-evidence-tracing.md)
5. [GitHub 展示与提交建议](./12-github-showcase.md)

如果要准备面试或复盘，则建议完整读完所有章节。
