from __future__ import annotations

import html
import json
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from app.core.config import Settings
from app.core.logger import get_logger, log_context
from app.core.exceptions import SearchBackendError
from app.tools.base import BaseTool

logger = get_logger(__name__)


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "MCP-style web search tool returning source URLs and paragraph-level snippets."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 3},
        },
        "required": ["query"],
    }

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    async def run(self, tool_input: str, **kwargs: object) -> str:
        query = " ".join(tool_input.split())
        max_results = int(kwargs.get("max_results", 3))
        search_depth = str(kwargs.get("search_depth", "advanced"))
        provider = str(kwargs.get("provider") or self.settings.web_search_provider)
        attempts: list[tuple[str, str]] = []

        for backend in self._provider_chain(provider):
            try:
                if backend == "tavily":
                    return await self._search_tavily(query, max_results, search_depth)
                if backend == "searxng":
                    return await self._search_searxng(query, max_results)
                if backend == "playwright":
                    return await self._search_playwright(query, max_results)
                if backend == "duckduckgo":
                    return await self._search_duckduckgo(query, max_results)
            except SearchBackendError as exc:
                attempts.append((backend, str(exc)))
                logger.warning(
                    "web_search_backend_failed",
                    extra=log_context(
                        query=query,
                        requested_provider=provider,
                        backend=backend,
                        error=str(exc),
                    ),
                )
                continue

        failed = "; ".join(f"{name}: {error}" for name, error in attempts) or "no search backend available"
        raise SearchBackendError(f"All search backends failed: {failed}")

    def _provider_chain(self, provider: str) -> list[str]:
        if provider == "duckduckgo":
            return ["duckduckgo"]
        if provider == "playwright":
            return ["playwright", "duckduckgo"]
        if provider == "searxng":
            return ["searxng", "playwright", "duckduckgo"] if self.settings.searxng_url else ["playwright", "duckduckgo"]
        if provider == "tavily":
            return ["tavily", "duckduckgo"] if self.settings.tavily_api_key else ["duckduckgo"]

        chain: list[str] = []
        if self.settings.searxng_url:
            chain.append("searxng")
        chain.append("playwright")
        chain.append("duckduckgo")
        return chain

    # ------------------------------------------------------------------
    # SearXNG API
    # ------------------------------------------------------------------

    async def _search_searxng(self, query: str, max_results: int) -> str:
        if not self.settings.searxng_url:
            raise SearchBackendError("SearXNG URL is not configured.")

        base_url = self.settings.searxng_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=self.settings.web_search_timeout_seconds) as client:
                response = await client.get(
                    f"{base_url}/search",
                    params={"q": query, "format": "json"},
                    headers={"User-Agent": self.settings.web_search_user_agent},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise SearchBackendError(f"SearXNG HTTP error: {exc}") from exc

        try:
            results = []
            for index, item in enumerate(data.get("results", [])[:max_results], start=1):
                url = str(item.get("url") or "")
                if not url.startswith("http"):
                    continue
                content = self._clean_text(str(item.get("content") or item.get("snippet") or ""))
                results.append(
                    {
                        "title": str(item.get("title") or f"Search result {index}"),
                        "url": url,
                        "paragraphs": [{"paragraph_id": "snippet-1", "text": content[:1200]}] if content else [],
                        "score": float(item.get("score") or max(0.1, 1.0 - index * 0.1)),
                        "provider": "searxng",
                        "domain": urlparse(url).netloc,
                    }
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise SearchBackendError(f"SearXNG response parse error: {exc}") from exc

        if not results:
            raise SearchBackendError("SearXNG returned no usable results.")

        return json.dumps({"query": query, "provider": "searxng", "results": results}, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Tavily API
    # ------------------------------------------------------------------

    async def _search_tavily(self, query: str, max_results: int, search_depth: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.settings.web_search_timeout_seconds) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": self.settings.tavily_api_key,
                        "query": query,
                        "search_depth": search_depth,
                        "max_results": max_results,
                        "include_answer": False,
                        "include_raw_content": False,
                    },
                    headers={"User-Agent": self.settings.web_search_user_agent},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise SearchBackendError(f"Tavily HTTP error: {exc}") from exc

        try:
            results = []
            for index, item in enumerate(data.get("results", [])[:max_results], start=1):
                content = self._clean_text(str(item.get("content", "")))
                results.append(
                    {
                        "title": str(item.get("title") or f"Search result {index}"),
                        "url": str(item.get("url") or ""),
                        "paragraphs": [{"paragraph_id": "snippet-1", "text": content[:1200]}] if content else [],
                        "score": float(item.get("score") or max(0.1, 1.0 - index * 0.1)),
                        "provider": "tavily",
                    }
                )
        except (KeyError, TypeError, ValueError) as exc:
            raise SearchBackendError(f"Tavily response parse error: {exc}") from exc

        return json.dumps({"query": query, "provider": "tavily", "results": results}, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Playwright 本地浏览器搜索
    # ------------------------------------------------------------------

    async def _search_playwright(self, query: str, max_results: int) -> str:
        try:
            from playwright.async_api import TimeoutError as PWTimeout
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise SearchBackendError(
                "playwright 未安装。运行: pip install playwright && playwright install chromium"
            ) from exc

        # 使用 DDG HTML 版：比 JS 版轻量，domcontentloaded 即可提取结果
        search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        results: list[dict] = []

        try:
            async with async_playwright() as pw:
                launcher = getattr(pw, self.settings.playwright_browser)
                browser = await launcher.launch(
                    headless=self.settings.playwright_headless,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                try:
                    page = await browser.new_page(user_agent=self.settings.web_search_user_agent)
                    await page.goto(
                        search_url,
                        wait_until="domcontentloaded",
                        timeout=self.settings.web_search_timeout_seconds * 1000,
                    )
                    results = await self._playwright_extract_ddg(page, max_results)
                finally:
                    await browser.close()
        except PWTimeout as exc:
            raise SearchBackendError(f"Playwright navigation timed out: {exc}") from exc
        except Exception as exc:
            # playwright 内部错误（browser crash、OS 限制等）
            raise SearchBackendError(f"Playwright error: {exc}") from exc

        if not results:
            raise SearchBackendError("Playwright search returned no results.")

        return json.dumps({"query": query, "provider": "playwright", "results": results}, ensure_ascii=False)

    async def _playwright_extract_ddg(self, page: object, max_results: int) -> list[dict]:
        results: list[dict] = []
        items = await page.query_selector_all(".result")  # type: ignore[attr-defined]
        for item in items:
            if len(results) >= max_results:
                break
            try:
                anchor = await item.query_selector(".result__a")
                if not anchor:
                    continue
                title = (await anchor.inner_text()).strip()
                raw_href = await anchor.get_attribute("href") or ""
                # DDG HTML 版使用 /l/?uddg=<encoded-real-url> redirect
                decoded = unquote(raw_href)
                qs = parse_qs(urlparse(decoded).query)
                url = qs.get("uddg", [""])[0] or (raw_href if raw_href.startswith("http") else "")
                if not title or not url.startswith("http"):
                    continue
                snippet_el = await item.query_selector(".result__snippet")
                snippet = (await snippet_el.inner_text()).strip() if snippet_el else ""
                results.append({
                    "title": title[:200],
                    "url": url,
                    "paragraphs": [{"paragraph_id": "snippet-1", "text": snippet[:1200]}] if snippet else [],
                    "score": round(0.88 - len(results) * 0.05, 2),
                    "provider": "playwright",
                    "domain": urlparse(url).netloc,
                })
            except Exception:
                continue
        return results

    async def _playwright_extract_generic(self, page: object, max_results: int) -> list[dict]:
        results: list[dict] = []
        seen: set[str] = set()
        blocked = {"duckduckgo.com", "google.com", "www.google.com", "bing.com", "www.bing.com"}
        links = await page.query_selector_all("a[href^='http']")  # type: ignore[attr-defined]
        for link in links:
            if len(results) >= max_results:
                break
            try:
                href = await link.get_attribute("href") or ""
                if not href.startswith("http") or href in seen:
                    continue
                parsed = urlparse(href)
                if parsed.hostname in blocked:
                    continue
                seen.add(href)
                title = (await link.inner_text()).strip()
                if len(title) < 8:
                    continue
                results.append({
                    "title": title[:200],
                    "url": href,
                    "paragraphs": [],
                    "score": round(0.85 - len(results) * 0.05, 2),
                    "provider": "playwright",
                    "domain": parsed.netloc,
                })
            except Exception:
                continue
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo HTTP 抓取
    # ------------------------------------------------------------------

    async def _search_duckduckgo(self, query: str, max_results: int) -> str:
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.web_search_timeout_seconds, follow_redirects=True
            ) as client:
                response = await client.get(url, headers={"User-Agent": self.settings.web_search_user_agent})
                response.raise_for_status()
                body = response.text
        except httpx.HTTPError as exc:
            raise SearchBackendError(f"DuckDuckGo HTTP error: {exc}") from exc

        # 分别提取标题链接和摘要，避免跨元素 .*? 匹配失效
        title_matches = re.findall(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, flags=re.S
        )
        snippet_matches = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|span|div)', body, flags=re.S
        )
        results = []
        for i, (raw_href, raw_title) in enumerate(title_matches[:max_results]):
            # DDG HTML 版链接是 /l/?uddg=<encoded-real-url>，解码取真实地址
            decoded = unquote(html.unescape(raw_href))
            qs = parse_qs(urlparse(decoded).query)
            real_url = qs.get("uddg", [""])[0] or (raw_href if raw_href.startswith("http") else "")
            if not real_url.startswith("http"):
                continue
            title = self._clean_text(raw_title)
            snippet = self._clean_text(snippet_matches[i]) if i < len(snippet_matches) else ""
            results.append(
                {
                    "title": title or f"Search result {i + 1}",
                    "url": real_url,
                    "paragraphs": [{"paragraph_id": "snippet-1", "text": snippet[:1200]}] if snippet else [],
                    "score": round(0.85 - i * 0.05, 2),
                    "provider": "duckduckgo",
                    "domain": urlparse(real_url).netloc,
                }
            )
        if not results:
            raise SearchBackendError("No DuckDuckGo results parsed.")
        return json.dumps({"query": query, "provider": "duckduckgo", "results": results}, ensure_ascii=False)

    @staticmethod
    def _clean_text(value: str) -> str:
        without_tags = re.sub(r"<[^>]+>", " ", value)
        return " ".join(html.unescape(without_tags).split())
