from types import SimpleNamespace

from app.tools.web_fetch import WebFetchTool


def test_extract_crawl4ai_markdown_prefers_fit_markdown() -> None:
    result = SimpleNamespace(
        markdown=SimpleNamespace(
            fit_markdown="This is the preferred markdown body.",
            raw_markdown="Fallback markdown body.",
        )
    )

    extracted = WebFetchTool._extract_crawl4ai_markdown(result)

    assert extracted == "This is the preferred markdown body."


def test_paragraphs_from_text_splits_long_markdown_content() -> None:
    text = (
        "First paragraph with enough words to become evidence. " * 6
        + "\n\n"
        + "Second paragraph with enough words to become another evidence chunk. " * 6
    )

    paragraphs = WebFetchTool._paragraphs_from_text(text)

    assert len(paragraphs) >= 1
    assert paragraphs[0]["paragraph_id"] == "p1"
    assert "Second paragraph" in paragraphs[0]["text"]


def test_paragraphs_from_text_skips_cookie_and_navigation_noise() -> None:
    text = (
        "选择您的 Cookie 首选项 我们使用必要 Cookie 和类似工具提供我们的网站和服务。"
        "\n\n"
        "注册登录 社区 首页 关于 常见问题 文档建议反馈控制台"
        "\n\n"
        + "真正的正文内容应该保留下来并且持续描述Agent沙箱的实现方式风险边界隔离策略权限控制工程路径和真实使用场景" * 6
    )

    paragraphs = WebFetchTool._paragraphs_from_text(text)

    assert len(paragraphs) == 1
    assert "真正的正文内容应该保留下来" in paragraphs[0]["text"]
