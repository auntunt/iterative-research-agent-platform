from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.token_counter import count_tokens

ModelTier = Literal["nano", "medium", "large"]

# agent 角色的基准分（越高越倾向 large 模型）
_ROLE_BASE: dict[str, float] = {
    "planner": 0.55,
    "critic": 0.50,
    "writer": 0.50,
    "researcher": 0.25,
}

# token 数对应的分数
_TOKEN_SCORE_TABLE: list[tuple[int, float]] = [
    (8_000, 0.80),
    (4_000, 0.65),
    (2_000, 0.50),
    (1_000, 0.35),
    (500,   0.20),
    (0,     0.05),
]

# 各信号权重
_W_TOKENS = 0.35
_W_ROUND = 0.20
_W_EVIDENCE = 0.20
_W_ROLE = 0.15
_W_FAIL_RATE = 0.10

_TIER_THRESHOLDS: list[tuple[float, ModelTier]] = [
    (0.65, "large"),
    (0.35, "medium"),
    (0.0,  "nano"),
]


@dataclass
class ComplexityScore:
    value: float                      # [0, 1]
    tier: ModelTier
    signals: dict[str, float] = field(default_factory=dict)
    reason: str = ""


def _token_score(token_count: int) -> float:
    for threshold, score in _TOKEN_SCORE_TABLE:
        if token_count >= threshold:
            return score
    return 0.05


def _round_score(round_index: int, max_rounds: int) -> float:
    if max_rounds <= 0:
        return 0.0
    return min(1.0, round_index / max_rounds)


def _evidence_score(card_count: int) -> float:
    # 卡片越多，综合推理越复杂
    if card_count >= 20:
        return 0.90
    if card_count >= 10:
        return 0.65
    if card_count >= 5:
        return 0.40
    return 0.10


def _fail_rate_score(provider_stats: dict[str, dict[str, int]], preferred: str) -> float:
    stats = provider_stats.get(preferred, {})
    total = stats.get("success", 0) + stats.get("failure", 0)
    if total == 0:
        return 0.0
    fail_rate = stats.get("failure", 0) / total
    # 历史失败率高 → 倾向换更大模型 → 给高分
    return min(1.0, fail_rate * 2)


def _select_tier(score: float) -> ModelTier:
    for threshold, tier in _TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return "nano"


def estimate(
    prompt: str,
    agent_role: str,
    context: dict[str, Any],
    provider_stats: dict[str, dict[str, int]] | None = None,
    model: str = "gpt-4o",
) -> ComplexityScore:
    """
    根据多个信号估算任务复杂度，返回模型 tier 建议。

    Args:
        prompt:        完整 prompt 文本（用于 token 计数）
        agent_role:    agent 名称（planner / researcher / critic / writer）
        context:       当前 graph state 的 context 字典
        provider_stats: LLMRouter.provider_stats（用于历史失败率）
        model:         用于 tiktoken 的目标模型名
    """
    role = agent_role.lower()

    # --- Signal 1: prompt token 数 ---
    token_count = count_tokens(prompt, model)
    s_tokens = _token_score(token_count)

    # --- Signal 2: 当前研究轮次 ---
    round_index = int(context.get("round_index", 0))
    max_rounds = int(context.get("max_research_rounds", 2))
    s_round = _round_score(round_index, max_rounds)

    # --- Signal 3: 已积累证据卡片数量 ---
    cards = context.get("evidence_cards", [])
    s_evidence = _evidence_score(len(cards) if isinstance(cards, list) else 0)

    # --- Signal 4: agent 角色基准分 ---
    s_role = _ROLE_BASE.get(role, 0.30)

    # --- Signal 5: 历史失败率 ---
    preferred = context.get("preferred_provider", role)
    s_fail = _fail_rate_score(provider_stats or {}, preferred)

    weighted = (
        s_tokens   * _W_TOKENS
        + s_round  * _W_ROUND
        + s_evidence * _W_EVIDENCE
        + s_role   * _W_ROLE
        + s_fail   * _W_FAIL_RATE
    )
    value = round(min(1.0, max(0.0, weighted)), 4)
    tier = _select_tier(value)

    reason_parts = [
        f"tokens={token_count}({s_tokens:.2f})",
        f"round={round_index}/{max_rounds}({s_round:.2f})",
        f"evidence={len(cards) if isinstance(cards, list) else 0}({s_evidence:.2f})",
        f"role={role}({s_role:.2f})",
        f"fail_rate={s_fail:.2f}",
    ]
    reason = f"score={value:.3f} [{'; '.join(reason_parts)}] → {tier}"

    return ComplexityScore(
        value=value,
        tier=tier,
        signals={
            "token_count": float(token_count),
            "token_score": s_tokens,
            "round_score": s_round,
            "evidence_score": s_evidence,
            "role_score": s_role,
            "fail_rate_score": s_fail,
        },
        reason=reason,
    )
