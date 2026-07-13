"""Dataset loading and deterministic scoring for Agent Loop traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .models import LoopEvalCase


def load_loop_cases(path: str | Path) -> list[LoopEvalCase]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("loop evaluation dataset must be a JSON array")
    cases = [LoopEvalCase.model_validate(item) for item in raw]
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("loop evaluation case_id values must be unique")
    return cases


def score_loop_trace(
    case: LoopEvalCase,
    events: Iterable[Any],
    *,
    final_status: str,
) -> dict[str, Any]:
    normalized = [_event_values(event) for event in events]
    event_types = [event["type"] for event in normalized]
    iterations = [
        int(event["iteration"])
        for event in normalized
        if event["type"] == "agent.loop.completed"
    ]
    loop_count = max(iterations, default=0)
    missing_events = [
        event_type
        for event_type in case.expected.required_events
        if event_type not in event_types
    ]
    checks = {
        "min_iterations": loop_count >= case.expected.min_iterations,
        "max_iterations": loop_count <= case.expected.max_iterations,
        "final_status": final_status == case.expected.final_status,
        "required_events": not missing_events,
    }
    return {
        "case_id": case.case_id,
        "passed": all(checks.values()),
        "loop_count": loop_count,
        "final_status": final_status,
        "missing_events": missing_events,
        "checks": checks,
    }


def _event_values(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return {
            "type": str(event.get("type", "")),
            "iteration": int(event.get("iteration", 0) or 0),
        }
    return {
        "type": str(getattr(event, "type", "")),
        "iteration": int(getattr(event, "iteration", 0) or 0),
    }
