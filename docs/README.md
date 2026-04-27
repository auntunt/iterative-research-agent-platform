# 演示素材

运行应用后，可以把中文研究工作台的截图和录屏放在这里。

对外文档：

- [`gitbook/README.md`](./gitbook/README.md)：完整 GitBook 风格工程设计手册，覆盖 Agent 开发设计、RAG、记忆与上下文、多 Agent 编排、工具调用、状态管理、模型路由、证据追踪、可观测性和 GitHub 展示建议。

- [`agent_development_talk.md`](./agent_development_talk.md)：围绕本项目讲解大模型 Agent 开发、LangGraph 编排、Agent loop、工具调用、上下文管理、RAG、持久化和 GPT-Image-2 流程图提示词。

生成串讲图片：

```bash
OPENAI_API_KEY=... .venv/bin/python scripts/generate_talk_images.py
```

未生成 GPT-Image-2 图片时，串讲文档会先使用本地 SVG 流程图；脚本成功生成 PNG 后会自动把文档图片引用切换到 PNG。

建议文件：

- `ui-screenshot.png`
- `research-report-demo.gif`
- `trace-timeline.png`

README 会引用这个目录作为项目展示素材。
