from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.core.config import Settings

_ROLLING_SUMMARY_PREFIX = "rolling_summary:"
_COMPRESSION_STATS_PREFIX = "compression_stats:"


class MemoryStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self._events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._kv: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._long_term_path = Path(settings.long_term_memory_path if settings else "data/long_term_memory.json")
        self._long_term_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_long_term()

    async def append(self, task_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            self._events[task_id].append(event)

    async def get(self, task_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            return list(self._events.get(task_id, []))

    async def clear(self, task_id: str) -> None:
        async with self._lock:
            self._events.pop(task_id, None)

    async def remember(self, key: str, value: Any) -> None:
        async with self._lock:
            self._kv[key] = value
            self._persist_long_term()

    async def recall(self, key: str) -> Any:
        async with self._lock:
            return self._kv.get(key)

    async def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        async with self._lock:
            lowered = query.lower()
            matches = {
                key: value
                for key, value in self._kv.items()
                if lowered in key.lower() or lowered in json.dumps(value, default=str).lower()
            }
            return dict(list(matches.items())[:limit])

    # ------------------------------------------------------------------
    # 滚动摘要接口
    # ------------------------------------------------------------------

    async def get_rolling_summary(self, task_id: str) -> str:
        async with self._lock:
            return str(self._kv.get(f"{_ROLLING_SUMMARY_PREFIX}{task_id}", ""))

    async def set_rolling_summary(self, task_id: str, summary: str) -> None:
        async with self._lock:
            self._kv[f"{_ROLLING_SUMMARY_PREFIX}{task_id}"] = summary
            self._persist_long_term()

    async def get_compression_stats(self, task_id: str) -> dict[str, int]:
        async with self._lock:
            return dict(self._kv.get(f"{_COMPRESSION_STATS_PREFIX}{task_id}", {"compression_count": 0, "tokens_saved": 0}))

    async def update_compression_stats(self, task_id: str, compression_count: int, tokens_saved: int) -> None:
        async with self._lock:
            key = f"{_COMPRESSION_STATS_PREFIX}{task_id}"
            existing = dict(self._kv.get(key, {}))
            existing["compression_count"] = existing.get("compression_count", 0) + compression_count
            existing["tokens_saved"] = existing.get("tokens_saved", 0) + tokens_saved
            self._kv[key] = existing
            self._persist_long_term()

    def _load_long_term(self) -> None:
        if not self._long_term_path.exists():
            return
        try:
            self._kv = json.loads(self._long_term_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self._kv = {}

    def _persist_long_term(self) -> None:
        self._long_term_path.write_text(
            json.dumps(self._kv, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
