"""Tests for provider usage normalization and experiment cost estimation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from wotagent.telemetry import estimate_model_cost, extract_usage_with_cost


def test_deepseek_flash_cost_uses_cache_hit_miss_and_output_rates() -> None:
    message = SimpleNamespace(
        usage_metadata={
            "input_tokens": 1_000_000,
            "output_tokens": 100_000,
            "total_tokens": 1_100_000,
        },
        response_metadata={
            "model_name": "deepseek-v4-flash",
            "token_usage": {
                "prompt_cache_hit_tokens": 800_000,
                "prompt_cache_miss_tokens": 200_000,
            },
        },
    )

    usage = extract_usage_with_cost(message)

    assert usage["cache_hit_tokens"] == 800_000
    assert usage["cache_miss_tokens"] == 200_000
    assert usage["estimated_cost"] == pytest.approx(0.416)
    assert usage["pricing_available"] is True
    assert usage["pricing_source"] == "model_table"


def test_missing_cache_breakdown_conservatively_prices_all_input_as_miss() -> None:
    message = SimpleNamespace(
        usage_metadata={"input_tokens": 1_000, "output_tokens": 100},
        response_metadata={"model_name": "deepseek-v4-flash"},
    )

    usage = extract_usage_with_cost(message)

    assert usage["cache_hit_tokens"] == 0
    assert usage["cache_miss_tokens"] == 1_000
    assert usage["estimated_cost"] == pytest.approx(0.0012)


def test_unknown_model_marks_pricing_unavailable() -> None:
    estimate = estimate_model_cost(
        "unpriced-model",
        cache_hit_tokens=0,
        cache_miss_tokens=1_000,
        output_tokens=100,
    )

    assert estimate["estimated_cost"] == 0
    assert estimate["pricing_available"] is False


def test_environment_prices_override_model_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_CACHE_HIT_PRICE_CNY_PER_MILLION", "1")
    monkeypatch.setenv("LLM_CACHE_MISS_PRICE_CNY_PER_MILLION", "2")
    monkeypatch.setenv("LLM_OUTPUT_PRICE_CNY_PER_MILLION", "3")

    estimate = estimate_model_cost(
        "custom/model",
        cache_hit_tokens=1_000_000,
        cache_miss_tokens=1_000_000,
        output_tokens=1_000_000,
    )

    assert estimate["estimated_cost"] == pytest.approx(6.0)
    assert estimate["pricing_available"] is True
    assert estimate["pricing_source"] == "environment"
