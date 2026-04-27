from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

try:
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False

_CJK_RANGE = re.compile(r"[一-鿿぀-ヿ가-힯]")
_CHINESE_CHARS_PER_TOKEN = 1.5


@lru_cache(maxsize=16)
def _get_encoding(model: str) -> Any:
    """返回 tiktoken encoding，不支持的 model 回退到 cl100k_base。"""
    if not _TIKTOKEN_AVAILABLE:
        return None
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """
    精确计算 text 在给定 model 下的 token 数。

    优先使用 tiktoken；未安装 tiktoken 时，对中日韩字符按 1.5 字/token
    估算，其余按空格分词。永远返回 >= 1。
    """
    if not text:
        return 0

    enc = _get_encoding(model)
    if enc is not None:
        return max(1, len(enc.encode(text)))

    # Fallback：中文字符 / 1.5 + 剩余空格词
    cjk_chars = len(_CJK_RANGE.findall(text))
    rest = _CJK_RANGE.sub("", text)
    word_tokens = max(0, len(rest.split()))
    return max(1, int(cjk_chars / _CHINESE_CHARS_PER_TOKEN) + word_tokens)


def estimate_cost(token_count: int, cost_per_1k: float) -> float:
    return round((token_count / 1000) * cost_per_1k, 6)
