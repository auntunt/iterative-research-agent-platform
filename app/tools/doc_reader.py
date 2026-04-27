from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import Settings
from app.tools.base import BaseTool, ToolExecutionError


class DocumentReaderTool(BaseTool):
    name = "doc_reader"
    description = "Reads text documents from the configured document root."

    def __init__(self, settings: Settings) -> None:
        self.root = Path(settings.document_root).resolve()

    async def run(self, tool_input: str, **kwargs: object) -> str:
        await asyncio.sleep(0)
        requested = (self.root / tool_input.strip()).resolve()
        if not str(requested).startswith(str(self.root)):
            raise ToolExecutionError("Document path escapes configured document root.")
        if not requested.exists() or not requested.is_file():
            raise ToolExecutionError(f"Document not found: {tool_input}")
        if requested.suffix.lower() not in {".txt", ".md", ".json", ".csv", ".py"}:
            raise ToolExecutionError(f"Unsupported document type: {requested.suffix}")
        return requested.read_text(encoding="utf-8", errors="replace")[:12000]
