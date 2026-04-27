from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.core.config import Settings
from app.tools.base import BaseTool, ToolExecutionError


class PythonExecTool(BaseTool):
    name = "python_exec"
    description = "Executes short Python snippets in a subprocess with timeout and temporary isolation."

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run(self, tool_input: str, **kwargs: object) -> str:
        code = tool_input.strip()
        if not code:
            return "No code supplied."
        if self._looks_dangerous(code):
            raise ToolExecutionError("Rejected code containing unsafe filesystem or process operations.")

        with tempfile.TemporaryDirectory(prefix="matp-python-") as tmp:
            script = Path(tmp) / "snippet.py"
            script.write_text(code, encoding="utf-8")
            process = await asyncio.create_subprocess_exec(
                "python",
                str(script),
                cwd=tmp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.settings.python_exec_timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise ToolExecutionError("Python execution timed out.") from exc

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            raise ToolExecutionError(err or f"Python exited with code {process.returncode}.")
        return out or "Python executed successfully with no stdout."

    @staticmethod
    def _looks_dangerous(code: str) -> bool:
        lowered = code.lower()
        blocked = [
            "subprocess",
            "os.system",
            "shutil.rmtree",
            "socket",
            "requests.",
            "httpx.",
            "pathlib.path('/",
            "open('/",
        ]
        return any(token in lowered for token in blocked)
