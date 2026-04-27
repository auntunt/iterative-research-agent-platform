from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "docs" / "assets" / "talk-diagrams"
DOC_PATH = ROOT / "docs" / "agent_development_talk.md"
API_URL = "https://api.openai.com/v1/images/generations"


FIGURES: list[dict[str, str]] = [
    {
        "slug": "01-system-overview",
        "title": "系统总览图",
        "prompt": """请绘制一张 16:9 横版技术架构流程图，主题是“迭代式深度研究 Agent 平台”。
风格：干净的工程白板风格，中文标签，浅色背景，线条清晰，适合投影演示。
请画出从左到右的主流程：
用户输入 -> 前端研究工作台 -> FastAPI API -> 任务队列 -> LangGraph Orchestrator -> Planner -> 并行 Researcher -> Critic -> Writer -> Markdown 研究报告。
在 Researcher 下方画出工具链：SearXNG 搜索、Playwright 备份、DuckDuckGo 兜底、Crawl4AI 正文抓取、Web Fetch 清洗。
在 Orchestrator 下方画出存储层：SQLite 任务库、Trace 日志、MemoryStore、RAG 向量库。
用不同颜色区分“控制流”“数据流”“持久化”。
不要画人物，不要画营销插画，不要使用复杂背景。""",
    },
    {
        "slug": "02-task-lifecycle",
        "title": "任务生命周期",
        "prompt": """请绘制一张“深度研究任务生命周期”的流程图，16:9 横版。
从左到右展示阶段：提交问题、创建任务、进入队列、Planner 拆题、并行 Researcher、搜索工具、正文抓取、证据卡片、Critic 审查、追加检索或写作、Writer 生成报告、持久化和导出。
需要突出两个循环：
第一处循环：Critic 判断证据不足时回到 Researcher。
第二处循环：任务完成后证据进入 RAG 知识库，后续任务可命中缓存。
视觉风格：正式技术分享幻灯片，浅灰背景，蓝绿色控制流，橙色反馈循环，紫色存储层。
中文标签要短，不要超过 8 个字。""",
    },
    {
        "slug": "03-agent-loop",
        "title": "Agent Loop",
        "prompt": """请绘制一张“Agent Loop: Plan -> Act -> Observe”的闭环图。
画面中心是一个圆形循环，三个节点依次为：Plan 规划、Act 执行、Observe 观察。
在 Act 节点旁边画两个分支：LLM 生成、工具调用。
在 Observe 节点旁边画三个检查项：结果是否为空、工具是否成功、是否需要重试。
右侧画出统一输出 AgentRun，包含 output、llm_usage、tool_calls、routing_decisions。
风格：极简工程图，中文标签，适合在讲 Agent 抽象时使用。""",
    },
    {
        "slug": "04-langgraph-state-machine",
        "title": "LangGraph 状态机",
        "prompt": """请绘制一张 LangGraph 状态机图，16:9 横版。
节点包括：plan、research、critic、human_review、write、END。
主路径：plan -> research -> critic -> write -> END。
从 critic 画三条条件边：
1. 证据充分 -> write
2. 证据不足且未到轮次上限 -> research
3. 轮次耗尽或质量低 -> human_review -> write
在 research 节点内部用小图标表达“并行 Researcher”。
在图底部标出状态对象 ResearchGraphState，包含 task_id、context、topics、pending_topics、evidence_cards、assessment、round_index。
风格：专业软件架构图，中文说明，线条清晰。""",
    },
    {
        "slug": "05-parallel-research",
        "title": "并行 Researcher",
        "prompt": """请绘制一张“开放问题到并行 Researcher”的流程图。
左侧是用户问题，中间是 Planner 拆解，右侧展开 5 个并行研究主题：
概念边界、经验证据、权衡风险、工程实现、趋势展望。
每个主题连接到一个 Researcher worker，再汇总到 Evidence Cards。
强调“并行执行”和“主题覆盖”两个概念。
风格：技术讲解用图，浅色背景，节点整齐，中文标签。""",
    },
    {
        "slug": "06-search-fetch-toolchain",
        "title": "搜索和抓取工具链",
        "prompt": """请绘制一张“Researcher 工具调用链路”图。
从 Researcher 开始，先进入 web_search。
web_search 内部按顺序展示：SearXNG 主搜索 -> Playwright 备份 -> DuckDuckGo 兜底。
搜索结果输出 URL 列表，再进入 web_fetch。
web_fetch 内部展示：Crawl4AI 正文抓取 -> Markdown 清洗 -> 段落切分；失败时退回 httpx + HTML 解析。
最后输出 EvidenceCard，字段包括 claim、summary、url、paragraph_id、snippet、confidence。
请用不同颜色区分“搜索”“抓取”“证据结构化”。""",
    },
    {
        "slug": "07-evidence-card-pipeline",
        "title": "证据卡片生成",
        "prompt": """请绘制一张“网页内容到证据卡片”的转换图。
左侧是网页正文 Markdown，中间是段落切分和噪音过滤，右侧是结构化 EvidenceCard。
EvidenceCard 画成一个数据卡片，展示字段：claim、summary、citation.url、paragraph_id、snippet、confidence。
请突出 summary 是研究摘要，snippet 是原始摘录。
风格：数据管道图，清晰、正式、中文标签。""",
    },
    {
        "slug": "08-critic-feedback-loop",
        "title": "Critic 反馈闭环",
        "prompt": """请绘制一张 Critic 反馈闭环图。
输入是 Evidence Cards，进入 Critic 评分模块。
评分维度包括：覆盖度、忠实度、相关性。
如果证据充分，箭头指向 Writer。
如果证据不足，箭头返回 Researcher，并生成 follow-up topics。
如果达到最大轮次，箭头进入 Human Review，再进入 Writer。
请把“质量门控”作为图的核心视觉概念。""",
    },
    {
        "slug": "09-report-generation",
        "title": "报告生成",
        "prompt": """请绘制一张“证据卡片到正式研究报告”的流程图。
左侧是多张 EvidenceCard，中间是 Writer 综合写作，右侧是 Markdown 研究报告。
在 Writer 内部展示两步：引用草稿生成、最终报告润色。
报告结构展示为：摘要、引言、正文、局限、结论、引用索引。
请突出“中间证据结构”和“最终读者产物”的区别。
风格：正式研究报告工作流图，中文标签。""",
    },
    {
        "slug": "10-context-management",
        "title": "上下文管理",
        "prompt": """请绘制一张“Agent 上下文管理分层图”。
从底到顶画四层：
1. 原始事件和工具输出
2. 证据窗口 evidence_window
3. 滚动摘要 rolling_summary
4. 长期记忆 long_term_memory
旁边画出 token budget，表示只有必要内容进入 LLM prompt。
请展示每个 Agent step 结束后触发 summarize，再写入 MemoryStore。
风格：分层架构图，中文标签，清晰适合讲解。""",
    },
    {
        "slug": "11-rag-cache-loop",
        "title": "RAG 缓存闭环",
        "prompt": """请绘制一张“RAG 缓存闭环”图。
左侧是新研究主题，先进入 RAG 预查询。
如果命中，直接生成 knowledge_base 类型的 EvidenceCard。
如果未命中，进入 Web Research，生成新的 EvidenceCard。
任务完成后，EvidenceCard 进入 semantic chunker、embedder、vector store。
最后从 vector store 画一条回到 RAG 预查询的循环箭头。
请突出“RAG 是研究证据缓存层，而不是直接替代 Agent”。""",
    },
    {
        "slug": "12-persistence-observability",
        "title": "持久化和观测",
        "prompt": """请绘制一张“Agent 平台持久化与可观测性”架构图。
中心是 Orchestrator。
左侧是 Agent steps：Planner、Researcher、Critic、Writer。
右侧画四个存储：SQLite tasks、SQLite steps、SQLite logs、Artifacts。
底部画 MemoryStore 和 LangGraph Checkpoint。
前端工作台从这些存储读取任务状态、报告、证据卡片、时间线和日志。
风格：系统观测架构图，简洁，中文标签。""",
    },
]


def build_prompt(raw_prompt: str) -> str:
    return (
        raw_prompt.strip()
        + "\n\n统一要求：输出为 16:9 横版 PNG，适合放入 Markdown 文档和技术分享幻灯片。"
        + "文字必须尽量清晰，避免小字密集堆叠。不要添加水印、logo 或无关装饰。"
    )


async def generate_one(
    client: httpx.AsyncClient,
    api_key: str,
    figure: dict[str, str],
    *,
    model: str,
    size: str,
    quality: str,
    force: bool,
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{figure['slug']}.png"
    if output_path.exists() and not force:
        print(f"skip existing: {output_path.relative_to(ROOT)}")
        return output_path

    payload: dict[str, Any] = {
        "model": model,
        "prompt": build_prompt(figure["prompt"]),
        "n": 1,
        "size": size,
        "quality": quality,
    }
    response = await client.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    response.raise_for_status()
    data = response.json()["data"][0]
    if data.get("b64_json"):
        image_bytes = base64.b64decode(data["b64_json"])
    elif data.get("url"):
        image_response = await client.get(data["url"])
        image_response.raise_for_status()
        image_bytes = image_response.content
    else:
        raise RuntimeError(f"No image payload returned for {figure['slug']}: {json.dumps(data)[:500]}")

    output_path.write_bytes(image_bytes)
    print(f"generated: {output_path.relative_to(ROOT)}")
    return output_path


async def main() -> int:
    parser = argparse.ArgumentParser(description="Generate talk diagrams with OpenAI Image API.")
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="1536x864")
    parser.add_argument("--quality", default="medium")
    parser.add_argument("--only", nargs="*", help="Optional figure slugs to generate.")
    parser.add_argument("--force", action="store_true", help="Regenerate existing files.")
    parser.add_argument("--no-update-md", action="store_true", help="Do not switch Markdown image links to generated PNG files.")
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("MATP_OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY or MATP_OPENAI_API_KEY is required.", file=sys.stderr)
        return 2

    selected = FIGURES
    if args.only:
        wanted = set(args.only)
        selected = [figure for figure in FIGURES if figure["slug"] in wanted]
        missing = wanted - {figure["slug"] for figure in selected}
        if missing:
            print(f"Unknown figure slug(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2

    async with httpx.AsyncClient(timeout=180.0) as client:
        generated: list[Path] = []
        for figure in selected:
            generated.append(
                await generate_one(
                    client,
                    api_key,
                    figure,
                    model=args.model,
                    size=args.size,
                    quality=args.quality,
                    force=args.force,
                )
            )
    if generated and not args.no_update_md:
        update_markdown_links([path.stem for path in generated])
    return 0


def update_markdown_links(slugs: list[str]) -> None:
    if not DOC_PATH.exists():
        return
    text = DOC_PATH.read_text(encoding="utf-8")
    for slug in slugs:
        text = text.replace(
            f"./assets/talk-diagrams/{slug}.svg",
            f"./assets/talk-diagrams/{slug}.png",
        )
    DOC_PATH.write_text(text, encoding="utf-8")
    print(f"updated markdown links: {DOC_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
