from __future__ import annotations

import asyncio
import json

from app.tools.base import BaseTool


class MarkdownRenderTool(BaseTool):
    name = "markdown_render"
    description = "Validates and renders Markdown report structure for downstream delivery."
    input_schema = {
        "type": "object",
        "properties": {
            "markdown": {"type": "string"},
        },
        "required": ["markdown"],
    }

    async def run(self, tool_input: str, **kwargs: object) -> str:
        await asyncio.sleep(0)
        markdown = tool_input.strip()
        headings = [line for line in markdown.splitlines() if line.startswith("#")]
        citations = markdown.count("](")
        return json.dumps(
            {
                "valid": bool(markdown and headings),
                "headings": len(headings),
                "citations": citations,
                "characters": len(markdown),
            },
            ensure_ascii=False,
        )
