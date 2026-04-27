import pytest
import json

from app.core.config import Settings
from app.core.exceptions import SearchBackendError
from app.tools.web_search import WebSearchTool


@pytest.mark.asyncio
async def test_playwright_provider_falls_back_to_duckduckgo(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = WebSearchTool(Settings(web_search_provider="playwright"))

    async def fail_playwright(self, query: str, max_results: int) -> str:
        raise SearchBackendError("playwright unavailable")

    async def ok_duckduckgo(self, query: str, max_results: int) -> str:
        return json.dumps({"query": query, "provider": "duckduckgo", "results": [{"title": "Result", "url": "https://example.com", "paragraphs": []}]})

    monkeypatch.setattr(WebSearchTool, "_search_playwright", fail_playwright)
    monkeypatch.setattr(WebSearchTool, "_search_duckduckgo", ok_duckduckgo)

    payload = json.loads(await tool.run("agent research", provider="playwright"))

    assert payload["provider"] == "duckduckgo"


@pytest.mark.asyncio
async def test_searxng_provider_falls_back_to_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = WebSearchTool(Settings(web_search_provider="searxng", searxng_url="http://127.0.0.1:8080"))

    async def fail_searxng(self, query: str, max_results: int) -> str:
        raise SearchBackendError("searxng unavailable")

    async def ok_playwright(self, query: str, max_results: int) -> str:
        return json.dumps({"query": query, "provider": "playwright", "results": [{"title": "Result", "url": "https://example.com", "paragraphs": []}]})

    monkeypatch.setattr(WebSearchTool, "_search_searxng", fail_searxng)
    monkeypatch.setattr(WebSearchTool, "_search_playwright", ok_playwright)

    payload = json.loads(await tool.run("agent research", provider="searxng"))

    assert payload["provider"] == "playwright"


@pytest.mark.asyncio
async def test_duckduckgo_provider_raises_when_backend_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = WebSearchTool(Settings(web_search_provider="duckduckgo"))

    async def fail_duckduckgo(self, query: str, max_results: int) -> str:
        raise SearchBackendError("ddg unavailable")

    monkeypatch.setattr(WebSearchTool, "_search_duckduckgo", fail_duckduckgo)

    with pytest.raises(SearchBackendError, match="All search backends failed"):
        await tool.run("agent research", provider="duckduckgo")
