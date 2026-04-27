from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.core.logger import get_logger, log_context
from app.models.schemas import LLMUsage

logger = get_logger(__name__)


class BaseLLM(Protocol):
    provider: str
    model: str

    async def generate(self, prompt: str, context: LLMContext | None = None) -> str:
        ...


@dataclass
class LLMContext:
    task: str
    agent: str
    complexity: str = "simple"
    excluded_providers: set[str] | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class RoutingDecision:
    """记录每次 LLM 调用的模型选择决策，供前端可视化。"""
    agent: str
    provider: str
    model: str
    tier: str                               # nano / medium / large
    complexity_score: float
    signals: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    estimated_cost_usd: float = 0.0
    fallback_used: bool = False


class LocalLLM:
    provider = "local"

    def __init__(self, model: str) -> None:
        self.model = model

    async def generate(self, prompt: str, context: LLMContext | None = None) -> str:
        await asyncio.sleep(0)
        compact = " ".join(prompt.strip().split())
        agent = context.agent if context else "agent"
        return (
            f"Local model response for {agent} in the iterative research workflow. "
            f"Prompt summary: {compact[:700]}"
        )


class OpenAILLM:
    def __init__(self, model: str, api_key: str | None, timeout: float, provider: str = "openai") -> None:
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    async def generate(self, prompt: str, context: LLMContext | None = None) -> str:
        if not self.api_key:
            return await LocalLLM(f"{self.model}-mock").generate(prompt)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, model: str, api_key: str | None, timeout: float) -> None:
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    async def generate(self, prompt: str, context: LLMContext | None = None) -> str:
        if not self.api_key:
            return await LocalLLM(f"{self.model}-mock").generate(prompt)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1200,
                    "temperature": 0.2,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            data = response.json()
            return "".join(block.get("text", "") for block in data.get("content", []))


class VLLMLLM:
    provider = "qwen"

    def __init__(self, model: str, base_url: str | None, api_key: str | None, timeout: float) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    async def generate(self, prompt: str, context: LLMContext | None = None) -> str:
        if not self.base_url:
            return await LocalLLM(f"{self.model}-mock").generate(prompt, context)

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]


class OpenRouterLLM:
    """OpenAI-compatible Chat Completions via OpenRouter (https://openrouter.ai)."""

    provider = "openrouter"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.timeout = settings.llm_timeout_seconds
        self.model = settings.openrouter_model

    def _resolve_model(self, context: LLMContext | None) -> str:
        if not context:
            return self._settings.openrouter_model
        # per-agent 模型配置优先级最高
        agent_model = self._settings.openrouter_agent_models.get(context.agent.lower())
        if agent_model:
            return agent_model
        meta = context.metadata or {}
        tokens = int(meta.get("estimated_prompt_tokens", 0))
        if context.complexity == "complex":
            return self._settings.openrouter_model_complex or self._settings.openrouter_model
        if tokens > self._settings.token_threshold_for_complex:
            return self._settings.openrouter_model_complex or self._settings.openrouter_model
        agent = context.agent.lower()
        if agent in {"critic", "planner"} and context.complexity != "simple":
            return self._settings.openrouter_model_complex or self._settings.openrouter_model
        return self._settings.openrouter_model

    async def generate(self, prompt: str, context: LLMContext | None = None) -> str:
        model = self._resolve_model(context)
        if not self._settings.openrouter_api_key:
            return await LocalLLM(f"{model}-mock").generate(prompt)

        url = f"{self._settings.openrouter_base_url.rstrip('/')}/chat/completions"
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
        }
        if self._settings.openrouter_http_referer:
            headers["HTTP-Referer"] = self._settings.openrouter_http_referer
        if self._settings.openrouter_app_title:
            headers["X-OpenRouter-Title"] = self._settings.openrouter_app_title

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]


class LLMRouter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.providers: dict[str, BaseLLM] = {
            "local": LocalLLM(settings.local_model),
            "openai": OpenAILLM(settings.openai_model, settings.openai_api_key, settings.llm_timeout_seconds),
            "openai_nano": OpenAILLM(
                settings.openai_nano_model,
                settings.openai_api_key,
                settings.llm_timeout_seconds,
                provider="openai_nano",
            ),
            "openai_mini": OpenAILLM(
                settings.openai_mini_model,
                settings.openai_api_key,
                settings.llm_timeout_seconds,
                provider="openai_mini",
            ),
            "anthropic": AnthropicLLM(settings.anthropic_model, settings.anthropic_api_key, settings.llm_timeout_seconds),
            "openrouter": OpenRouterLLM(settings),
            "qwen": VLLMLLM(
                settings.qwen_model,
                settings.qwen_vllm_base_url,
                settings.qwen_vllm_api_key,
                settings.llm_timeout_seconds,
            ),
        }
        self.provider_stats: dict[str, dict[str, int]] = {
            name: {"success": 0, "failure": 0} for name in self.providers
        }

    def route(self, context: LLMContext | dict[str, Any]) -> BaseLLM:
        ctx = self._normalize_context(context)
        for provider_name in self._candidate_providers(ctx):
            provider = self.providers.get(provider_name)
            if provider:
                logger.info(
                    "llm_provider_selected",
                    extra=log_context(
                        provider=provider.provider,
                        model=provider.model,
                        agent=ctx.agent,
                        complexity=ctx.complexity,
                    ),
                )
                return provider

        return self.providers["local"]

    async def generate(
        self,
        prompt: str,
        context: LLMContext | dict[str, Any],
    ) -> tuple[str, LLMUsage, RoutingDecision]:
        """
        生成文本并返回 (text, usage, routing_decision)。

        routing_decision 记录了本次模型选择的完整推理过程。
        """
        ctx = self._normalize_context(context)
        candidates = self._candidate_providers(ctx)
        excluded = set(ctx.excluded_providers or set())
        last_error: Exception | None = None
        started = time.perf_counter()

        # 生成路由决策（复杂度估算）
        routing = self._make_routing_decision(prompt, ctx)

        for index, provider_name in enumerate(candidates):
            if provider_name in excluded:
                continue
            provider = self.providers.get(provider_name)
            if not provider:
                continue
            logger.info(
                "llm_provider_selected",
                extra=log_context(
                    provider=provider.provider,
                    model=provider.model,
                    agent=ctx.agent,
                    complexity=ctx.complexity,
                    tier=routing.tier,
                    complexity_score=routing.complexity_score,
                    fallback_index=index,
                ),
            )
            try:
                text = await asyncio.wait_for(
                    provider.generate(prompt, ctx),
                    timeout=self.settings.llm_timeout_seconds,
                )
                self.provider_stats[provider.provider]["success"] += 1
                latency = time.perf_counter() - started
                usage = self._usage(provider, prompt, text, latency, fallback_used=index > 0)
                logger.info("llm_call_succeeded", extra=log_context(**usage.model_dump()))
                routing.provider = provider.provider
                routing.model = provider.model
                routing.estimated_cost_usd = usage.estimated_cost
                routing.fallback_used = index > 0
                return text, usage, routing
            except Exception as exc:
                last_error = exc
                self.provider_stats[provider.provider]["failure"] += 1
                excluded.add(provider.provider)
                logger.exception(
                    "llm_provider_failed",
                    extra=log_context(provider=provider.provider, model=provider.model, error=str(exc)),
                )
                continue

        fallback = self.providers["local"]
        text = await fallback.generate(prompt, ctx)
        latency = time.perf_counter() - started
        usage = self._usage(fallback, prompt, text, latency, fallback_used=True)
        logger.warning(
            "llm_all_candidates_failed_using_local",
            extra=log_context(error=str(last_error), **usage.model_dump()),
        )
        routing.provider = fallback.provider
        routing.model = fallback.model
        routing.estimated_cost_usd = usage.estimated_cost
        routing.fallback_used = True
        return text, usage, routing

    def _make_routing_decision(self, prompt: str, ctx: LLMContext) -> RoutingDecision:
        """调用 ComplexityEstimator 生成路由决策对象。"""
        from app.core.complexity_estimator import estimate

        meta = ctx.metadata or {}
        score = estimate(
            prompt=prompt,
            agent_role=ctx.agent,
            context=meta,
            provider_stats=self.provider_stats,
        )
        return RoutingDecision(
            agent=ctx.agent,
            provider="",        # 填充于实际调用成功后
            model="",
            tier=score.tier,
            complexity_score=score.value,
            signals=score.signals,
            reason=score.reason,
        )

    def _candidate_providers(self, context: LLMContext) -> list[str]:
        excluded = self._expand_provider_blacklist(
            set(context.excluded_providers or set()) | set(self.settings.provider_blacklist)
        )
        preferred = self._preferred_provider(context)
        candidates = [preferred, *self.settings.fallback_providers, self.settings.default_provider, "local"]

        ranked = []
        for name in dict.fromkeys(candidates):
            if name in excluded:
                continue
            stats = self.provider_stats.get(name, {"success": 0, "failure": 0})
            total = stats["success"] + stats["failure"]
            success_rate = stats["success"] / total if total else 1.0
            ranked.append((success_rate, name))
        ranked.sort(reverse=True)
        if preferred not in excluded:
            ranked.sort(key=lambda item: item[1] != preferred)
        return [name for _, name in ranked] or ["local"]

    def _usage(self, provider: BaseLLM, prompt: str, text: str, latency: float, fallback_used: bool) -> LLMUsage:
        from app.core.token_counter import count_tokens

        prompt_tokens = count_tokens(prompt, provider.model)
        completion_tokens = count_tokens(text, provider.model)
        total_tokens = prompt_tokens + completion_tokens
        cost_per_1k = self.settings.cost_per_1k_tokens.get(provider.provider, 0.0)
        return LLMUsage(
            provider=provider.provider,
            model=provider.model,
            latency=latency,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost=(total_tokens / 1000) * cost_per_1k,
            fallback_used=fallback_used,
        )

    def _preferred_provider(self, context: LLMContext) -> str:
        override = self.settings.agent_provider_overrides.get(context.agent.lower())
        if override:
            return override
        metadata = context.metadata or {}
        prompt_tokens = int(metadata.get("estimated_prompt_tokens", 0))
        if prompt_tokens > self.settings.token_threshold_for_complex:
            return self.settings.complex_task_provider
        if context.complexity == "complex":
            return self.settings.complex_task_provider
        if context.agent.lower() in {"critic", "planner"} and context.complexity != "simple":
            return self.settings.complex_task_provider
        return self.settings.simple_task_provider

    def _normalize_context(self, context: LLMContext | dict[str, Any]) -> LLMContext:
        if isinstance(context, LLMContext):
            return context
        return LLMContext(
            task=str(context.get("task", "")),
            agent=str(context.get("agent", "")),
            complexity=str(context.get("complexity", "simple")),
            excluded_providers=set(context.get("excluded_providers", set())),
            metadata=dict(context.get("metadata", {})),
        )

    @staticmethod
    def _expand_provider_blacklist(excluded: set[str]) -> set[str]:
        expanded = set(excluded)
        if "openai" in expanded:
            expanded.update({"openai_nano", "openai_mini"})
        return expanded

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        from app.core.token_counter import count_tokens
        return count_tokens(text)
