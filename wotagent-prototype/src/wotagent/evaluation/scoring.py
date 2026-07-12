"""Deterministic BFCL/HomeBench-style plan scoring."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..domain import TaskPlan
from .models import EvalAction, EvalCase


def score_plan(case: EvalCase, plan: TaskPlan) -> dict[str, Any]:
    predicted = [EvalAction(device_id=s.device_id, action=s.action, input=s.input) for s in plan.steps]
    expected_counter = Counter(item.key() for item in case.expected_actions)
    predicted_counter = Counter(item.key() for item in predicted)
    true_positive = sum((expected_counter & predicted_counter).values())
    precision = true_positive / sum(predicted_counter.values()) if predicted_counter else 0.0
    recall = true_positive / sum(expected_counter.values()) if expected_counter else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "intent_correct": plan.intent == case.expected_intent,
        "exact_match": predicted_counter == expected_counter,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "expected_count": sum(expected_counter.values()),
        "predicted_count": sum(predicted_counter.values()),
    }
