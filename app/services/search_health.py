from __future__ import annotations

import time
from typing import Any

from app.core.config import Settings
from app.tools.web_search import WebSearchTool


class SearchHealthChecker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def check(self) -> dict[str, Any]:
        providers = {
            "tavily": await self._check_tavily(),
            "searxng": await self._check_searxng(),
            "playwright": await self._check_playwright(),
            "duckduckgo": await self._check_duckduckgo(),
        }
        selected = self.settings.web_search_provider
        if selected == "auto":
            if providers["searxng"]["available"]:
                active = "searxng"
            elif providers["playwright"]["available"]:
                active = "playwright"
            elif providers["duckduckgo"]["available"]:
                active = "duckduckgo"
            elif providers["tavily"]["available"]:
                active = "tavily"
            else:
                active = "unavailable"
        else:
            active = selected
        return {
            "configured_provider": selected,
            "active_provider": active,
            "providers": providers,
        }

    async def _check_tavily(self) -> dict[str, Any]:
        return {
            "available": bool(self.settings.tavily_api_key),
            "configured": bool(self.settings.tavily_api_key),
            "detail": "Tavily API key configured." if self.settings.tavily_api_key else "No Tavily API key configured.",
        }

    async def _check_searxng(self) -> dict[str, Any]:
        if not self.settings.searxng_url:
            return {
                "available": False,
                "configured": False,
                "detail": "No SearXNG URL configured.",
            }

        started = time.perf_counter()
        try:
            tool = WebSearchTool(self.settings)
            await tool._search_searxng("deep research health check", 1)
            return {
                "available": True,
                "configured": True,
                "latency": round(time.perf_counter() - started, 4),
                "detail": "SearXNG returned parseable JSON results.",
                "url": self.settings.searxng_url,
            }
        except Exception as exc:
            return {
                "available": False,
                "configured": True,
                "latency": round(time.perf_counter() - started, 4),
                "detail": str(exc),
                "url": self.settings.searxng_url,
            }

    async def _check_playwright(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            tool = WebSearchTool(self.settings)
            await tool._search_playwright("deep research health check", 1)
            return {
                "available": True,
                "configured": True,
                "latency": round(time.perf_counter() - started, 4),
                "detail": "Playwright returned parseable browser search results.",
            }
        except Exception as exc:
            return {
                "available": False,
                "configured": True,
                "latency": round(time.perf_counter() - started, 4),
                "detail": str(exc),
            }

    async def _check_duckduckgo(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            tool = WebSearchTool(self.settings)
            await tool._search_duckduckgo("deep research health check", 1)
            return {
                "available": True,
                "configured": True,
                "latency": round(time.perf_counter() - started, 4),
                "detail": "DuckDuckGo HTML search returned parseable results.",
            }
        except Exception as exc:
            return {
                "available": False,
                "configured": True,
                "latency": round(time.perf_counter() - started, 4),
                "detail": str(exc),
            }
