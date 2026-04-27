from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from app.models.schemas import ToolResult


class ToolExecutionError(RuntimeError):
    pass


class BaseTool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any] = {"type": "string"}

    async def __call__(self, tool_input: str, **kwargs: Any) -> ToolResult:
        started = time.perf_counter()
        try:
            output = await self.run(tool_input, **kwargs)
            return ToolResult(
                tool=self.name,
                success=True,
                output=output,
                latency=time.perf_counter() - started,
                metadata={**kwargs, "mcp_schema": self.mcp_schema()},
            )
        except Exception as exc:
            return ToolResult(
                tool=self.name,
                success=False,
                output=str(exc),
                latency=time.perf_counter() - started,
                metadata={**kwargs, "mcp_schema": self.mcp_schema()},
            )

    def mcp_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @abstractmethod
    async def run(self, tool_input: str, **kwargs: Any) -> str:
        ...
