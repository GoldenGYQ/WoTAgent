"""Token usage extraction and configurable model cost estimation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenPricing:
    cache_hit: float
    cache_miss: float
    output: float
    currency: str = "CNY"


# CNY per one million tokens. Environment overrides remain authoritative because
# provider prices and aliases can change independently of this prototype.
_MODEL_PRICING = {
    "deepseek-v4-flash": TokenPricing(cache_hit=0.02, cache_miss=1.0, output=2.0),
    "deepseek-chat": TokenPricing(cache_hit=0.02, cache_miss=1.0, output=2.0),
    "deepseek-reasoner": TokenPricing(cache_hit=0.02, cache_miss=1.0, output=2.0),
    "deepseek-v4-pro": TokenPricing(cache_hit=0.025, cache_miss=3.0, output=6.0),
}


def extract_usage_with_cost(message: Any) -> dict[str, Any]:
    """Normalize LangChain/provider usage metadata and attach a cost estimate."""
    usage = getattr(message, "usage_metadata", None) or {}
    response = getattr(message, "response_metadata", None) or {}
    raw = response.get("token_usage") or response.get("usage") or {}

    input_tokens = int(usage.get("input_tokens", raw.get("prompt_tokens", 0)) or 0)
    output_tokens = int(usage.get("output_tokens", raw.get("completion_tokens", 0)) or 0)
    total_tokens = int(
        usage.get("total_tokens", raw.get("total_tokens", input_tokens + output_tokens)) or 0
    )
    input_details = usage.get("input_token_details") or {}
    output_details = usage.get("output_token_details") or {}
    cache_hit_tokens = int(
        raw.get(
            "prompt_cache_hit_tokens",
            input_details.get("cache_read", input_details.get("cache_hit", 0)),
        )
        or 0
    )
    explicit_cache_miss = raw.get("prompt_cache_miss_tokens")
    cache_miss_tokens = (
        int(explicit_cache_miss or 0)
        if explicit_cache_miss is not None
        else max(input_tokens - cache_hit_tokens, 0)
    )
    reasoning_tokens = int(
        output_details.get("reasoning", raw.get("reasoning_tokens", 0)) or 0
    )
    model = str(response.get("model_name") or response.get("model") or "unknown")
    estimate = estimate_model_cost(
        model,
        cache_hit_tokens=cache_hit_tokens,
        cache_miss_tokens=cache_miss_tokens,
        output_tokens=output_tokens,
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
        "cache_hit_tokens": cache_hit_tokens,
        "cache_miss_tokens": cache_miss_tokens,
        **estimate,
    }


def estimate_model_cost(
    model: str,
    *,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    output_tokens: int,
) -> dict[str, Any]:
    pricing, source = _resolve_pricing(model)
    if pricing is None:
        return {
            "estimated_cost": 0.0,
            "currency": "CNY",
            "pricing_available": False,
            "pricing_source": "unavailable",
        }
    cost = (
        cache_hit_tokens * pricing.cache_hit
        + cache_miss_tokens * pricing.cache_miss
        + output_tokens * pricing.output
    ) / 1_000_000
    return {
        "estimated_cost": cost,
        "currency": pricing.currency,
        "pricing_available": True,
        "pricing_source": source,
    }


def _resolve_pricing(model: str) -> tuple[TokenPricing | None, str]:
    override_names = (
        "LLM_CACHE_HIT_PRICE_CNY_PER_MILLION",
        "LLM_CACHE_MISS_PRICE_CNY_PER_MILLION",
        "LLM_OUTPUT_PRICE_CNY_PER_MILLION",
    )
    override_values = [os.getenv(name) for name in override_names]
    if all(value not in (None, "") for value in override_values):
        return TokenPricing(*(float(value) for value in override_values)), "environment"

    normalized = model.lower().strip().split("/")[-1]
    pricing = _MODEL_PRICING.get(normalized)
    return pricing, "model_table" if pricing is not None else "unavailable"
