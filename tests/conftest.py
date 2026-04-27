"""Tests must not inherit MATP_* from the developer's project .env."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_matp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("MATP_"):
            monkeypatch.delenv(key, raising=False)

    # Force local providers — overrides any .env placeholder values that
    # pydantic-settings would otherwise pick up when Settings() is re-instantiated.
    monkeypatch.setenv("MATP_OPENAI_API_KEY", "")
    monkeypatch.setenv("MATP_ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("MATP_OPENROUTER_API_KEY", "")
    monkeypatch.setenv("MATP_TAVILY_API_KEY", "")
    monkeypatch.setenv("MATP_DEFAULT_PROVIDER", "local")
    monkeypatch.setenv("MATP_SIMPLE_TASK_PROVIDER", "local")
    monkeypatch.setenv("MATP_COMPLEX_TASK_PROVIDER", "local")
    monkeypatch.setenv("MATP_FALLBACK_PROVIDERS", '["local"]')
    monkeypatch.setenv(
        "MATP_AGENT_PROVIDER_OVERRIDES",
        '{"planner":"local","researcher":"local","critic":"local","writer":"local"}',
    )
    monkeypatch.setenv("MATP_WEB_SEARCH_PROVIDER", "duckduckgo")
    monkeypatch.setenv("MATP_QUEUE_BACKEND", "memory")
    # 测试中禁用可选依赖，避免未安装的包造成超时
    monkeypatch.setenv("MATP_RAG_ENABLED", "false")
    # 清除 get_settings lru_cache，确保 monkeypatched 环境变量生效
    from app.core.config import get_settings
    get_settings.cache_clear()
