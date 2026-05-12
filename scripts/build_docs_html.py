#!/usr/bin/env python3
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
HTML_DIR = DOCS_DIR / "html"


@dataclass(frozen=True)
class DocSource:
    title: str
    path: Path
    group: str


DOC_SOURCES = [
    DocSource("技术导读", DOCS_DIR / "TECHNICAL_OVERVIEW.zh-CN.md", "快速导读"),
    DocSource("工程设计手册", DOCS_DIR / "gitbook" / "README.md", "快速导读"),
    DocSource("1. 系统总览", DOCS_DIR / "gitbook" / "01-system-overview.md", "工程设计"),
    DocSource("2. Agent 开发设计", DOCS_DIR / "gitbook" / "02-agent-development-design.md", "工程设计"),
    DocSource("3. 多 Agent 编排", DOCS_DIR / "gitbook" / "03-multi-agent-orchestration.md", "工程设计"),
    DocSource("4. 状态管理", DOCS_DIR / "gitbook" / "04-state-management.md", "工程设计"),
    DocSource("5. 工具调用", DOCS_DIR / "gitbook" / "05-tool-calling.md", "工程设计"),
    DocSource("6. RAG 知识系统", DOCS_DIR / "gitbook" / "06-rag-system.md", "工程设计"),
    DocSource("7. 记忆系统与上下文血统", DOCS_DIR / "gitbook" / "07-memory-and-context-lineage.md", "工程设计"),
    DocSource("8. 模型路由", DOCS_DIR / "gitbook" / "08-model-routing.md", "工程设计"),
    DocSource("9. 证据追踪", DOCS_DIR / "gitbook" / "09-evidence-tracing.md", "工程设计"),
    DocSource("10. 可观测性与产物", DOCS_DIR / "gitbook" / "10-observability-and-artifacts.md", "工程设计"),
    DocSource("11. API、前端与演示", DOCS_DIR / "gitbook" / "11-api-frontend-demo.md", "工程设计"),
    DocSource("12. GitHub 展示与提交建议", DOCS_DIR / "gitbook" / "12-github-showcase.md", "工程设计"),
]


@dataclass
class Heading:
    level: int
    text: str
    anchor: str
    doc_anchor: str


@dataclass
class RenderedDoc:
    source: DocSource
    anchor: str
    html: str
    headings: list[Heading]


def strip_inline_markdown(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("**", "").replace("__", "")
    return text.strip()


def slugify(text: str, fallback: str) -> str:
    cleaned = strip_inline_markdown(text).lower()
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "-", cleaned, flags=re.UNICODE).strip("-")
    return cleaned or fallback


def unique_anchor(base: str, used: set[str]) -> str:
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def render_inline(text: str) -> str:
    placeholders: list[str] = []

    def keep(match: re.Match[str]) -> str:
        placeholders.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"@@CODE{len(placeholders) - 1}@@"

    text = re.sub(r"`([^`]+)`", keep, text)
    escaped = html.escape(text)
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{html.escape(rewrite_link(m.group(2)), quote=True)}">{m.group(1)}</a>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", escaped)
    for index, value in enumerate(placeholders):
        escaped = escaped.replace(f"@@CODE{index}@@", value)
    return escaped


def rewrite_link(href: str) -> str:
    if href.startswith(("http://", "https://", "#", "mailto:")):
        return href
    base = href.split("#", 1)[0].strip()
    normalized = base.lstrip("./")
    if base in {"../README.md", "../../README.md"} or normalized in {"README.md"} and href.startswith("../"):
        return "../../README.md"
    if base in {"../.env.example", "../../.env.example"} or normalized in {".env.example", "env.example"} and href.startswith("../"):
        return "../../.env.example"
    if href.endswith(".md") or ".md#" in href:
        for source in DOC_SOURCES:
            rel = source.path.relative_to(DOCS_DIR).as_posix()
            candidates = {rel, source.path.name, f"./{rel}", f"./{source.path.name}"}
            if normalized in candidates or base in candidates:
                return f"#{slugify(source.title, source.path.stem)}"
        return "#"
    return href


def is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def render_table(lines: list[str]) -> str:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    headers = rows[0]
    body = rows[2:]
    parts = ["<div class=\"table-wrap\"><table>", "<thead><tr>"]
    parts.extend(f"<th>{render_inline(cell)}</th>" for cell in headers)
    parts.append("</tr></thead><tbody>")
    for row in body:
        parts.append("<tr>")
        for index in range(len(headers)):
            cell = row[index] if index < len(row) else ""
            parts.append(f"<td>{render_inline(cell)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def flush_paragraph(buffer: list[str], output: list[str]) -> None:
    if buffer:
        paragraph = " ".join(line.strip() for line in buffer).strip()
        if paragraph:
            output.append(f"<p>{render_inline(paragraph)}</p>")
        buffer.clear()


def render_markdown(source: DocSource, doc_index: int, used: set[str]) -> RenderedDoc:
    text = source.path.read_text(encoding="utf-8")
    lines = text.splitlines()
    doc_anchor = unique_anchor(slugify(source.title, f"doc-{doc_index}"), used)
    output = [
        (
            f'<article class="doc-section" id="{doc_anchor}" data-title="{html.escape(source.title, quote=True)}">'
            f'<div class="source-path">{html.escape(str(source.path.relative_to(ROOT)))}</div>'
        )
    ]
    headings: list[Heading] = []
    paragraph: list[str] = []
    index = 0
    in_code = False
    code_lang = ""
    code_lines: list[str] = []

    while index < len(lines):
        line = lines[index]

        if line.startswith("```"):
            if in_code:
                output.append(
                    f'<pre class="code-block" data-lang="{html.escape(code_lang)}">'
                    f"<code>{html.escape(chr(10).join(code_lines))}</code></pre>"
                )
                in_code = False
                code_lang = ""
                code_lines = []
            else:
                flush_paragraph(paragraph, output)
                in_code = True
                code_lang = line[3:].strip() or "text"
            index += 1
            continue

        if in_code:
            code_lines.append(line)
            index += 1
            continue

        if not line.strip():
            flush_paragraph(paragraph, output)
            index += 1
            continue

        if "|" in line and index + 1 < len(lines) and is_table_separator(lines[index + 1]):
            flush_paragraph(paragraph, output)
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table_lines.append(lines[index])
                index += 1
            output.append(render_table(table_lines))
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush_paragraph(paragraph, output)
            level = len(heading.group(1))
            title = strip_inline_markdown(heading.group(2))
            anchor = unique_anchor(slugify(title, f"h-{doc_index}-{len(headings)}"), used)
            headings.append(Heading(level=level, text=title, anchor=anchor, doc_anchor=doc_anchor))
            output.append(
                f'<h{level} id="{anchor}"><a class="anchor" href="#{anchor}" aria-label="章节链接">#</a>'
                f"{render_inline(heading.group(2))}</h{level}>"
            )
            index += 1
            continue

        quote = re.match(r"^>\s?(.*)$", line)
        if quote:
            flush_paragraph(paragraph, output)
            quote_lines = [quote.group(1)]
            index += 1
            while index < len(lines):
                next_quote = re.match(r"^>\s?(.*)$", lines[index])
                if not next_quote:
                    break
                quote_lines.append(next_quote.group(1))
                index += 1
            output.append(f"<blockquote>{render_inline(' '.join(quote_lines))}</blockquote>")
            continue

        list_match = re.match(r"^(\s*)([-*]|\d+\.)\s+(.+)$", line)
        if list_match:
            flush_paragraph(paragraph, output)
            ordered = bool(re.fullmatch(r"\d+\.", list_match.group(2)))
            tag = "ol" if ordered else "ul"
            items = [list_match.group(3)]
            index += 1
            while index < len(lines):
                next_match = re.match(r"^(\s*)([-*]|\d+\.)\s+(.+)$", lines[index])
                if not next_match or bool(re.fullmatch(r"\d+\.", next_match.group(2))) != ordered:
                    break
                items.append(next_match.group(3))
                index += 1
            output.append(f"<{tag}>")
            output.extend(f"<li>{render_inline(item)}</li>" for item in items)
            output.append(f"</{tag}>")
            continue

        paragraph.append(line)
        index += 1

    flush_paragraph(paragraph, output)
    output.append("</article>")
    return RenderedDoc(source=source, anchor=doc_anchor, html="\n".join(output), headings=headings)


def render_sidebar(docs: list[RenderedDoc]) -> str:
    parts = ['<nav class="sidebar-nav" aria-label="文档章节">']
    current_group = ""
    for doc in docs:
        if doc.source.group != current_group:
            if current_group:
                parts.append("</div>")
            current_group = doc.source.group
            parts.append(f'<div class="nav-group"><div class="nav-group-title">{html.escape(current_group)}</div>')
        parts.append(f'<a href="#{doc.anchor}" data-doc-link="{doc.anchor}">{html.escape(doc.source.title)}</a>')
    if current_group:
        parts.append("</div>")
    parts.append("</nav>")
    return "\n".join(parts)


def render_outline(docs: list[RenderedDoc]) -> str:
    parts = ['<nav class="outline-nav" aria-label="当前页目录">']
    for doc in docs:
        parts.append(f'<div class="outline-doc" data-outline-doc="{doc.anchor}">')
        parts.append(f'<a class="outline-doc-title" href="#{doc.anchor}">{html.escape(doc.source.title)}</a>')
        for heading in doc.headings:
            if heading.level <= 3:
                parts.append(
                    f'<a class="outline-level-{heading.level}" href="#{heading.anchor}">'
                    f"{html.escape(heading.text)}</a>"
                )
        parts.append("</div>")
    parts.append("</nav>")
    return "\n".join(parts)


def build_html(docs: list[RenderedDoc]) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>迭代式研究智能体平台 | HTML 文档</title>
    <link rel="stylesheet" href="./style.css" />
  </head>
  <body>
    <div class="layout">
      <aside class="sidebar">
        <a class="brand" href="#top">
          <span class="brand-mark">IR</span>
          <span>
            <strong>研究智能体文档</strong>
            <small>HTML 阅读版</small>
          </span>
        </a>
        <label class="search-label" for="navSearch">目录过滤</label>
        <input id="navSearch" class="nav-search" type="search" placeholder="输入章节关键词" />
        {render_sidebar(docs)}
      </aside>
      <main class="content" id="top">
        <header class="hero">
          <p class="eyebrow">Iterative Research Agent Platform</p>
          <h1>工程设计文档 HTML 阅读版</h1>
          <p>
            这一版把原有 Markdown/GitBook 内容整理成静态 HTML，提供固定章节导航、当前章节目录、
            表格与代码块优化，适合 GitHub Pages、浏览器本地阅读和项目复习。
          </p>
          <div class="hero-actions">
            <a href="#技术导读">从技术导读开始</a>
            <a href="#6-rag-知识系统">查看 RAG 知识系统</a>
          </div>
        </header>
        {"".join(doc.html for doc in docs)}
      </main>
      <aside class="outline">
        <div class="outline-title">章节目录</div>
        {render_outline(docs)}
      </aside>
    </div>
    <button class="mobile-toc" type="button" aria-label="打开目录">目录</button>
    <script src="./app.js"></script>
  </body>
</html>
"""


def main() -> None:
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    rendered = [render_markdown(source, index, used) for index, source in enumerate(DOC_SOURCES, start=1)]
    (HTML_DIR / "index.html").write_text(build_html(rendered), encoding="utf-8")
    print(f"generated {HTML_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
