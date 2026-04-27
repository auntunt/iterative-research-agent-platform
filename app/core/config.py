from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml


class Settings(BaseSettings):
    app_name: str = "迭代式深度研究智能体平台"
    environment: Literal["local", "dev", "staging", "prod"] = "local"
    config_file: str = "config/dev.yaml"
    log_level: str = "INFO"
    log_file: str = "logs/platform.log"

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None

    openai_model: str = "gpt-5"
    openai_nano_model: str = "gpt-5-nano"
    openai_mini_model: str = "gpt-5-mini"
    anthropic_model: str = "claude-3-5-sonnet-latest"
    local_model: str = "local-deterministic-v1"
    qwen_model: str = "Qwen2.5-72B-Instruct-INT4"
    qwen_vllm_base_url: str | None = None
    qwen_vllm_api_key: str | None = None
    tavily_api_key: str | None = None
    web_search_provider: Literal["auto", "tavily", "searxng", "playwright", "duckduckgo"] = "auto"
    web_search_timeout_seconds: float = 12.0
    web_search_user_agent: str = "IterativeResearchAgent/1.0"
    playwright_headless: bool = True
    playwright_browser: Literal["chromium", "firefox", "webkit"] = "chromium"
    searxng_url: str | None = None
    crawl4ai_base_directory: str = "data/crawl4ai"

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_model_complex: str | None = None
    openrouter_http_referer: str | None = None
    openrouter_app_title: str | None = None
    # 每个 agent 在 openrouter 内使用的具体模型（优先级高于 simple/complex 二档）
    openrouter_agent_models: dict[str, str] = Field(default_factory=dict)

    default_provider: str = "local"
    simple_task_provider: str = "local"
    complex_task_provider: str = "openai"
    fallback_providers: list[str] = Field(default_factory=lambda: ["anthropic", "local"])
    provider_blacklist: list[str] = Field(default_factory=list)
    llm_timeout_seconds: float = 30.0
    token_threshold_for_complex: int = 600
    cost_per_1k_tokens: dict[str, float] = Field(
        default_factory=lambda: {
            "local": 0.0,
            "openai": 0.01,
            "openai_nano": 0.00005,
            "openai_mini": 0.0006,
            "anthropic": 0.003,
            "openrouter": 0.00015,
            "qwen": 0.0001,
        }
    )
    agent_provider_overrides: dict[str, str] = Field(
        default_factory=lambda: {
            "planner": "openai",
            "researcher": "openai_nano",
            "critic": "qwen",
            "writer": "openai_mini",
        }
    )

    max_retries: int = 2
    retry_backoff_seconds: float = 0.1
    max_research_rounds: int = 2
    min_evidence_per_topic: int = 2
    evidence_window_size: int = 12
    max_sources_per_query: int = 4
    python_exec_timeout_seconds: int = 5
    document_root: str = "."

    database_url: str = "sqlite:///data/platform.db"
    queue_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    redis_queue_name: str = "matp:tasks"
    redis_reconnect_interval_seconds: float = 5.0
    redis_max_reconnect_interval_seconds: float = 60.0
    max_concurrent_tasks: int = 3
    max_queue_size: int = 100
    worker_poll_interval_seconds: float = 0.1
    long_term_memory_path: str = "data/long_term_memory.json"

    enabled_agents: dict[str, bool] = Field(
        default_factory=lambda: {
            "planner": True,
            "researcher": True,
            "critic": True,
            "writer": True,
        }
    )

    # --- Token 计数 ---
    tiktoken_fallback_ratio: float = 2.5

    # --- 上下文管理 ---
    context_max_tokens: int = 80_000
    context_reserve_tokens: int = 4_096
    context_summary_model: str = "openai_nano"

    # --- RAG ---
    rag_enabled: bool = True
    rag_chroma_persist_dir: str = "data/chroma"
    rag_embedding_model: str = "text-embedding-3-small"
    rag_local_embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    rag_min_cache_score: float = 0.80
    rag_chunk_max_tokens: int = 400
    rag_chunk_threshold: float = 0.75

    # --- LangGraph ---
    langgraph_checkpoint_db: str = "data/checkpoints.db"

    # --- Human-in-the-loop ---
    hil_min_confidence_threshold: float = 0.60
    hil_min_coverage_for_review: float = 0.60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MATP_",
        case_sensitive=False,
        extra="ignore",
)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    yaml_values = _load_yaml_config(settings.config_file)
    # 读取 .env 文件中显式设置的 key（不含默认值），YAML 不覆盖已在 .env 或 os.environ 中设置的值
    dot_env_keys = _dot_env_keys(settings.model_config.get("env_file", ".env"))
    for key, value in yaml_values.items():
        env_key = f"MATP_{key.upper()}"
        if env_key not in os.environ and env_key not in dot_env_keys and hasattr(settings, key):
            setattr(settings, key, value)
    return settings


def _dot_env_keys(env_file: str | None) -> set[str]:
    """返回 .env 文件中显式定义的 key 集合（大写）。"""
    if not env_file:
        return set()
    path = Path(env_file)
    if not path.exists():
        return set()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key = line.split("=", 1)[0].strip().upper()
            if key:
                keys.add(key)
    return keys


def _load_yaml_config(path: str) -> dict[str, object]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return _flatten_config(raw)


def _flatten_config(raw: dict[str, object]) -> dict[str, object]:
    flattened: dict[str, object] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                flattened[f"{key}_{nested_key}"] = nested_value
        else:
            flattened[key] = value

    aliases = {
        "llm_default_provider": "default_provider",
        "llm_simple_task_provider": "simple_task_provider",
        "llm_complex_task_provider": "complex_task_provider",
        "llm_fallback_providers": "fallback_providers",
        "llm_provider_blacklist": "provider_blacklist",
        "llm_timeout_seconds": "llm_timeout_seconds",
        "llm_token_threshold_for_complex": "token_threshold_for_complex",
        "llm_cost_per_1k_tokens": "cost_per_1k_tokens",
        "llm_openrouter_base_url": "openrouter_base_url",
        "llm_openrouter_model": "openrouter_model",
        "llm_openrouter_model_complex": "openrouter_model_complex",
        "llm_openrouter_http_referer": "openrouter_http_referer",
        "llm_openrouter_app_title": "openrouter_app_title",
        "llm_openrouter_agent_models": "openrouter_agent_models",
        "llm_openai_nano_model": "openai_nano_model",
        "llm_openai_mini_model": "openai_mini_model",
        "llm_qwen_model": "qwen_model",
        "llm_qwen_vllm_base_url": "qwen_vllm_base_url",
        "llm_qwen_vllm_api_key": "qwen_vllm_api_key",
        "llm_agent_provider_overrides": "agent_provider_overrides",
        "search_tavily_api_key": "tavily_api_key",
        "search_provider": "web_search_provider",
        "search_timeout_seconds": "web_search_timeout_seconds",
        "search_user_agent": "web_search_user_agent",
        "search_searxng_url": "searxng_url",
        "search_crawl4ai_base_directory": "crawl4ai_base_directory",
        "retry_max_retries": "max_retries",
        "retry_backoff_seconds": "retry_backoff_seconds",
        "research_max_rounds": "max_research_rounds",
        "research_min_evidence_per_topic": "min_evidence_per_topic",
        "research_evidence_window_size": "evidence_window_size",
        "research_max_sources_per_query": "max_sources_per_query",
        "queue_backend": "queue_backend",
        "queue_max_concurrent_tasks": "max_concurrent_tasks",
        "queue_max_queue_size": "max_queue_size",
        "queue_redis_url": "redis_url",
        "queue_redis_queue_name": "redis_queue_name",
        "agents_enabled": "enabled_agents",
    }
    return {aliases.get(key, key): value for key, value in flattened.items()}
