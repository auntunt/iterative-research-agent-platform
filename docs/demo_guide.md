# 现场演示指南

这个演示流程会启动 FastAPI 服务，按需通过 Cloudflare Tunnel 暴露 API，把公开 API 地址写入前端配置，运行中文研究任务，并保存结构化运行结果。

## 启动演示

在项目根目录执行：

```bash
scripts/demo_all_in_one.sh
```

脚本会启动 `uvicorn app.main:app --host 0.0.0.0 --port 8000`，等待 `/health` 就绪，启动 Cloudflare Tunnel，写入 `frontend/config.js`，打开前端，运行 `demo/tasks.json` 中的任务，并把结果保存到 `demo/results/`。

使用命名 Tunnel：

```bash
DEMO_TUNNEL_NAME=research-agent-demo \
DEMO_PUBLIC_API_URL=https://api-demo.example.com \
scripts/demo_all_in_one.sh
```

如果没有配置命名 Tunnel，脚本会回退到：

```bash
cloudflared tunnel --url http://localhost:8000
```

## 演示流程

1. API 启动，FastAPI lifespan 会启动队列 worker。
2. Cloudflare Tunnel 发布 API，脚本提取公开 URL。
3. `scripts/inject_api_url.sh` 将公开 API URL 写入 `frontend/config.js`。
4. 前端读取 `window.__APP_CONFIG__.API_BASE_URL` 并调用公开 API。
5. `scripts/run_demo.sh` 提交简单、中等和复杂三个中文研究任务。
6. 每个任务通过 `/task/{task_id}` 轮询，直到进入 `success` 或 `failed`。
7. Runner 保存任务状态和日志到 `demo/results/`，并打印最终摘要。

## 演示讲解要点

**Planner 与子主题：** Planner 将开放问题拆成 3-5 个研究主题，并记录任务图。

**LangGraph 编排：** 后端用 LangGraph `StateGraph` 表达 plan、research、critic、write 节点，Critic 的评估结果通过条件边决定进入下一轮检索还是进入写作。

**并行研究：** Researcher 针对每个主题调用搜索和抓取工具，每个结果都会被转化为带 URL 和段落级引用的证据卡片。

**Critic 闭环：** Critic 评估覆盖度、忠实度和答案相关性。证据不足时，它会生成 follow-up 主题并触发下一轮检索。

**模型路由：** 智能体不直接调用模型，而是交给 Router。默认策略是 Planner 用 GPT-5，Researcher 用 GPT-5-nano，Writer 用 GPT-5-mini，Critic 可接 Qwen/vLLM；没有 key 时会回退到本地 provider。

**搜索与抓取：** 推荐使用 `SearXNG` 作为主搜索源，`Playwright` 作为浏览器备份；正文抓取优先走 `Crawl4AI`。

**可观测性：** UI 和保存的 JSON 会展示 task_id、trace_id、步骤、工具 schema、provider 使用、队列状态、日志、重试、token 估算、延迟、成本和最终引用。

## 常用命令

对已有 API 运行所有演示任务：

```bash
BASE_URL=https://your-public-api.example.com scripts/run_demo.sh
```

运行单个任务：

```bash
scripts/run_demo.sh --task complex --base-url https://your-public-api.example.com
```

列出演示任务：

```bash
scripts/demo_cli.py list
```

通过 CLI 运行任务：

```bash
scripts/demo_cli.py --base-url https://your-public-api.example.com run complex
```

查看任务详情：

```bash
scripts/demo_cli.py --base-url https://your-public-api.example.com detail TASK_ID
```

查看日志：

```bash
scripts/demo_cli.py --base-url https://your-public-api.example.com logs --task-id TASK_ID
```

## 排查

**找不到 `cloudflared`：** 安装 Cloudflare Tunnel，并确认 `cloudflared` 在 `PATH` 中。

**命名 Tunnel 启动但没有识别 URL：** 命名 Tunnel 通常不会打印公开 hostname。请用 `DEMO_PUBLIC_API_URL` 指定已配置的 hostname。

**`/health` 一直未就绪：** 查看 `logs/demo.log` 和 `logs/platform.log`。常见原因是依赖缺失或 `8000` 端口被占用。

**前端调用了错误 API：** 重新执行 `scripts/inject_api_url.sh https://your-api-url` 并刷新浏览器。

**CLI 无法导入 `requests`：** 执行 `python -m pip install -r requirements.txt`。
