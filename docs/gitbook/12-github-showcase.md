# 12. GitHub 展示与提交建议

当前项目适合上传 GitHub，但建议以“练手型 Agent 工程项目”的口径展示。它的展示价值不在于 UI 多复杂，也不在于声称已经生产可用，而在于覆盖了 Agent 工程常见关键点：多 Agent 编排、工具调用、RAG、状态管理、模型路由、证据追踪、日志指标和产物导出。

## 上传前应保留的内容

建议提交：

- `app/`
- `frontend/`
- `config/`
- `demo/`
- `docs/`
- `evals/questions.json`
- `scripts/`
- `tests/`
- `requirements.txt`
- `Dockerfile`
- `docker-compose.yml`
- `README.md`
- `.env.example`
- `.gitignore`
- `LICENSE`

## 上传前应排除的内容

不要提交：

- `.env`
- `.venv/`
- `data/`
- `logs/`
- `artifacts/`
- `evals/results/`
- `__pycache__/`
- `.pytest_cache/`
- 本地数据库、checkpoint、日志和真实运行产物

当前 `.gitignore` 已经覆盖这些目录，上传前仍建议执行一次检查。

## 推荐仓库展示结构

GitHub README 建议保留短而清晰的项目介绍，详细设计放到 GitBook：

```text
README.md
docs/
  gitbook/
    README.md
    SUMMARY.md
    01-system-overview.md
    ...
```

README 里只需要说明：

- 项目是什么。
- 核心能力是什么。
- 如何运行。
- 文档入口在哪里。
- 演示截图或 GIF 在哪里。

## 初始化 Git 仓库

当前目录如果还不是 Git 仓库，可以执行：

```bash
cd /Users/auntlee/Desktop/简历项目/multi-agent-task-platform
git init
git add .
git status
git commit -m "docs: add gitbook architecture guide"
```

如果已经有 GitHub 空仓库，例如：

```text
https://github.com/<your-name>/multi-agent-task-platform.git
```

可以添加远程并推送：

```bash
git branch -M main
git remote add origin https://github.com/<your-name>/multi-agent-task-platform.git
git push -u origin main
```

## 提交前检查

建议执行：

```bash
git status --short
git check-ignore -v .env data/platform.db logs/platform.log
.venv/bin/python -m pytest -q
```

如果没有安装依赖或没有虚拟环境，可以至少执行：

```bash
python -m compileall app
```

## 展示时怎么讲

可以用下面这段作为项目介绍：

> 这是一个用于练习 Agent 工程能力的迭代式深度研究平台。项目本身不追求包装成成熟商业产品，而是把 Planner、Researcher、Critic、Writer 四类 Agent 串成一条完整研究链路。Planner 负责拆解研究主题，Researcher 并行调用搜索和抓取工具生成段落级证据卡片，Critic 判断证据覆盖是否充分，不足时触发追加检索，Writer 最后基于证据卡片生成带引用的中文 Markdown 报告。围绕这条主链路，项目还练习了模型路由、RAG 证据复用、任务状态持久化、Human-in-the-loop、日志指标和产物导出。

## 项目的真实定位

这个项目可以坦诚定位为练手和展示项目。它比较粗糙，仍然有不少工程细节可以继续打磨，但它不是玩具式单文件 Demo。它已经把 Agent 系统常见的多个核心边界串了起来：

- 有明确的 Agent 生命周期。
- 有编排层和状态机。
- 有工具抽象和工具调用记录。
- 有证据结构和引用追踪。
- 有 Critic 反馈循环。
- 有持久化任务状态。
- 有模型路由和 fallback。
- 有 RAG 知识复用。
- 有前端、API、测试和文档。

## 粗糙但有价值的地方

这个项目的价值不在于每个模块都做到最好，而在于它把多个技术点放到了同一条任务链路中：

- Agent 不是孤立函数，而是被 Orchestrator 编排。
- 工具调用不是临时脚本，而是有统一 `ToolResult` 和步骤记录。
- RAG 不是单独问答 Demo，而是接入到 Research 前置缓存和任务后置入库。
- 记忆不是一个概念名词，而是拆成事件、长期 KV、滚动摘要、上下文压缩和证据窗口。
- 证据不是普通文本片段，而是有 `card_id`、`topic_id`、URL、段落 ID 和 snippet。
- 模型不是硬编码一个 Provider，而是通过 Router 记录选择、fallback、成本和延迟。

后续如果继续加强，优先方向是：增加真实评测集、补更多端到端测试、完善权限隔离、给前端补一套更完整的演示截图和录屏。
