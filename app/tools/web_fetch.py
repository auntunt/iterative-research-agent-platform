from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

import httpx

from app.core.config import Settings
from app.core.logger import get_logger, log_context
from app.tools.base import BaseTool

logger = get_logger(__name__)


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = "Fetches a URL and returns normalized paragraphs with stable paragraph IDs."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
        },
        "required": ["url"],
    }

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    async def run(self, tool_input: str, **kwargs: object) -> str:
        url = tool_input.strip()
        if not url:
            raise ValueError("URL is required.")
        if not url.startswith(("http://", "https://")):
            raise ValueError("Only http(s) URLs are supported.")
        crawl4ai_payload = await self._fetch_with_crawl4ai(url)
        if crawl4ai_payload is not None:
            return crawl4ai_payload
        return await self._fetch_url(url)

    async def _fetch_with_crawl4ai(self, url: str) -> str | None:
        base_directory = str(Path(self.settings.crawl4ai_base_directory).resolve())
        os.makedirs(base_directory, exist_ok=True)
        os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", base_directory)
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
            from crawl4ai.content_filter_strategy import PruningContentFilter
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        except ImportError:
            return None

        markdown_generator = DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(
                threshold=0.48,
                threshold_type="fixed",
                min_word_threshold=40,
            )
        )
        browser_config = BrowserConfig(headless=True, verbose=False)
        run_config = CrawlerRunConfig(
            markdown_generator=markdown_generator,
            cache_mode=CacheMode.BYPASS,
            page_timeout=int(self.settings.web_search_timeout_seconds * 1000),
        )

        try:
            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(url=url, config=run_config)
        except Exception as exc:
            logger.warning(
                "crawl4ai_fetch_failed",
                extra=log_context(url=url, error=str(exc)),
            )
            return None

        if not getattr(result, "success", False):
            logger.warning(
                "crawl4ai_fetch_unsuccessful",
                extra=log_context(
                    url=url,
                    error=getattr(result, "error_message", "") or getattr(result, "error", ""),
                ),
            )
            return None

        markdown = self._extract_crawl4ai_markdown(result)
        paragraphs = self._paragraphs_from_text(markdown)
        if not paragraphs:
            logger.warning("crawl4ai_fetch_empty", extra=log_context(url=url))
            return None

        title = getattr(result, "metadata", {}).get("title") if getattr(result, "metadata", None) else ""
        final_url = getattr(result, "url", url) or url
        return json.dumps(
            {
                "url": final_url,
                "title": title or final_url,
                "paragraphs": paragraphs[:24],
                "content_type": "text/markdown",
                "provider": "crawl4ai",
            },
            ensure_ascii=False,
        )

    async def _fetch_url(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=self.settings.web_search_timeout_seconds, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": self.settings.web_search_user_agent})
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            text = response.text
        if "text/html" in content_type or "<html" in text[:500].lower():
            title, paragraphs = self._extract_html(text)
        else:
            title = url
            paragraphs = self._paragraphs_from_text(text)
        return json.dumps(
            {
                "url": str(response.url),
                "title": title or str(response.url),
                "paragraphs": paragraphs[:24],
                "content_type": content_type,
                "provider": "http_fetch",
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _extract_crawl4ai_markdown(result: object) -> str:
        markdown = getattr(result, "markdown", None)
        if isinstance(markdown, str):
            return markdown
        if markdown is not None:
            for attr in ("fit_markdown", "raw_markdown", "markdown", "content"):
                value = getattr(markdown, attr, None)
                if isinstance(value, str) and value.strip():
                    return value
        extracted = getattr(result, "extracted_content", None)
        return extracted if isinstance(extracted, str) else ""

    def _extract_html(self, body: str) -> tuple[str, list[dict[str, str]]]:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.I | re.S)
        title = self._clean_text(title_match.group(1)) if title_match else ""
        body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", body, flags=re.I | re.S)
        candidates = re.findall(r"<(?:p|li|h1|h2|h3)[^>]*>(.*?)</(?:p|li|h1|h2|h3)>", body, flags=re.I | re.S)
        return title, self._paragraphs_from_text("\n\n".join(self._clean_text(item) for item in candidates))

    @staticmethod
    def _paragraphs_from_text(text: str) -> list[dict[str, str]]:
        chunks = []
        for raw in re.split(r"\n\s*\n|(?<=[.!?。！？])\s+", text):
            cleaned = " ".join(raw.split())
            if WebFetchTool._is_noise_paragraph(cleaned):
                continue
            if len(cleaned) < 80:
                continue
            chunks.append({"paragraph_id": f"p{len(chunks) + 1}", "text": cleaned[:1600]})
            if len(chunks) >= 24:
                break
        if not chunks and text.strip():
            fallback = " ".join(text.split())
            if not WebFetchTool._is_noise_paragraph(fallback):
                chunks.append({"paragraph_id": "p1", "text": fallback[:1600]})
        return chunks

    @staticmethod
    def _clean_text(value: str) -> str:
        without_tags = re.sub(r"<[^>]+>", " ", value)
        return " ".join(html.unescape(without_tags).split())

    @staticmethod
    def _is_noise_paragraph(text: str) -> bool:
        lowered = text.lower()
        noise_signals = [
            "选择您的 cookie 首选项",
            "必要 cookie",
            "性能 cookie",
            "接受或拒绝",
            "要做出更详细的选择",
            "注册登录",
            "登录/注册",
            "社区首页",
            "跳转到主要内容",
            "文档建议反馈控制台",
            "如果您同意",
            "approved third parties",
            "cookie preferences",
            "accept or reject",
            "sign in",
            "log in",
        ]
        if any(signal in lowered for signal in noise_signals):
            return True
        navigation_hits = sum(
            token in lowered
            for token in ["首页", "社区", "关于", "常见问题", "控制台", "文档", "登录", "注册", "反馈"]
        )
        return navigation_hits >= 4 and len(text) < 400
