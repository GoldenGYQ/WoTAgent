from __future__ import annotations

import json

from wotagent.core.pipeline import _build_llm
from wotagent.runtime.run_runtime import (
    SUPERVISOR_EVIDENCE_PER_FINDING,
    SUPERVISOR_FINDINGS,
    SUPERVISOR_HISTORY_CHARACTERS,
    SUPERVISOR_HISTORY_MESSAGES,
    SUPERVISOR_TOOL_CALLS_PER_FINDING,
    _compact_findings,
    _compact_supervisor_context,
)


def test_supervisor_history_is_compacted_again_at_model_boundary() -> None:
    context = {
        "user_id": "JH-1",
        "role": "operator",
        "thinking": True,
        "history": [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"message-{index}-" + ("x" * 2000),
            }
            for index in range(20)
        ],
        "runtime": {"pending_goal": {"notes": "y" * 4000}},
    }

    compacted = _compact_supervisor_context(context)

    assert compacted["principal"] == {
        "user_id": "JH-1",
        "role": "operator",
    }
    assert len(compacted["history"]) == SUPERVISOR_HISTORY_MESSAGES
    assert all(
        len(item["content"]) <= SUPERVISOR_HISTORY_CHARACTERS
        for item in compacted["history"]
    )
    assert len(compacted["runtime"]["pending_goal"]["notes"]) == 500


def test_supervisor_findings_have_bounded_count_and_payload_size() -> None:
    findings = [
        {
            "agent": "health_agent",
            "objective": "检查健康状态" + ("o" * 2000),
            "status": "completed",
            "finding": "结论" + ("f" * 5000),
            "confidence": 0.9,
            "evidence": [
                {
                    "source": f"source-{evidence_index}",
                    "value": {
                        "samples": ["v" * 1000 for _ in range(20)],
                    },
                    "description": "d" * 1000,
                }
                for evidence_index in range(12)
            ],
            "recommended_actions": ["a" * 1000 for _ in range(12)],
            "unresolved_questions": ["q" * 1000 for _ in range(12)],
            "tool_calls": [
                {
                    "success": True,
                    "verified": True,
                    "tool_name": f"tool-{tool_index}",
                    "arguments": {"blob": "a" * 5000},
                    "observed_state": {"blob": "s" * 5000},
                }
                for tool_index in range(12)
            ],
        }
        for _ in range(12)
    ]

    compacted = _compact_findings(findings)
    rendered = json.dumps(compacted, ensure_ascii=False)

    assert len(compacted) == SUPERVISOR_FINDINGS
    assert all(
        len(item["evidence"]) == SUPERVISOR_EVIDENCE_PER_FINDING
        for item in compacted
    )
    assert all(
        len(item["tool_calls"]) == SUPERVISOR_TOOL_CALLS_PER_FINDING
        for item in compacted
    )
    assert len(rendered) < 20000
    assert "[truncated]" in rendered


def test_model_output_tokens_are_hard_capped_from_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("WOTAGENT_MAX_OUTPUT_TOKENS", "768")

    model = _build_llm(enable_thinking=False)

    assert model.max_tokens == 768
