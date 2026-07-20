from __future__ import annotations

import json

from wotagent.core.pipeline import _build_llm
from wotagent.runtime.run_runtime import (
    SUPERVISOR_EVIDENCE_PER_FINDING,
    SUPERVISOR_FINDINGS,
    SUPERVISOR_CONTEXT_SOFT_CHARACTERS,
    SUPERVISOR_HISTORY_CHARACTERS,
    SUPERVISOR_HISTORY_MESSAGES,
    SUPERVISOR_TOOL_CALLS_PER_FINDING,
    _compact_findings,
    _compact_supervisor_context,
    _supervisor_prompt_context,
)


def test_supervisor_history_is_compacted_again_at_model_boundary() -> None:
    context = {
        "principal": {
            "principal_id": "JH-1",
            "roles": ["operator"],
            "granted_capabilities": [
                "environment.control",
                "resident.context.read",
            ],
            "resource_ids": [
                "resident:elderly-001",
                "device:ac-002",
                "device:sensor-002",
            ],
            "relationships": [
                {
                    "relation": "caregiver_of",
                    "target_type": "resident",
                    "target_id": "elderly-001",
                    "target_label": "张爷爷",
                }
            ],
        },
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

    assert compacted["principal"] == context["principal"]
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


def test_supervisor_uses_full_context_before_soft_limit() -> None:
    context = {
        "principal": {
            "principal_id": "JH-1",
            "roles": ["operator"],
            "granted_capabilities": [
                "environment.control",
                "resident.context.read",
            ],
            "resource_ids": ["resident:elderly-001", "device:ac-002"],
            "relationships": [
                {
                    "relation": "caregiver_of",
                    "target_type": "resident",
                    "target_id": "elderly-001",
                    "target_label": "张爷爷",
                }
            ],
        },
        "source": {"type": "user", "target": {"type": "resident", "id": "elderly-001"}},
        "deployment": {
            "profile_id": "eldercare",
            "domain_id": "eldercare",
            "device_ids": ["ac-002"],
        },
        "history": [
            {"role": "user", "content": "把张爷爷房间温度调到 24 度"},
            {"role": "assistant", "content": "好的，我先确认当前房间环境。"},
        ],
        "runtime": {
            "pending_goal": {"user_message": "把张爷爷房间温度调到 24 度"},
        },
    }

    prompt_context, compacted, characters = _supervisor_prompt_context(context)

    assert compacted is False
    assert characters < SUPERVISOR_CONTEXT_SOFT_CHARACTERS
    assert prompt_context["principal"] == context["principal"]
    assert prompt_context["history"] == context["history"]
    assert prompt_context["deployment"] == context["deployment"]


def test_supervisor_compacts_context_after_soft_limit() -> None:
    context = {
        "principal": {
            "principal_id": "JH-1",
            "roles": ["operator"],
            "granted_capabilities": [
                "environment.control",
                "resident.context.read",
            ],
            "resource_ids": [f"device:ac-{index:03d}" for index in range(24)],
            "relationships": [
                {
                    "relation": "caregiver_of",
                    "target_type": "resident",
                    "target_id": "elderly-001",
                    "target_label": "张爷爷",
                }
            ],
        },
        "history": [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"message-{index}-" + ("x" * 3000),
            }
            for index in range(20)
        ],
        "runtime": {"pending_goal": {"notes": "y" * 5000}},
    }

    prompt_context, compacted, characters = _supervisor_prompt_context(context)

    assert compacted is True
    assert characters > SUPERVISOR_CONTEXT_SOFT_CHARACTERS
    assert prompt_context["principal"] == context["principal"]
    assert len(prompt_context["history"]) == SUPERVISOR_HISTORY_MESSAGES
    assert len(prompt_context["runtime"]["pending_goal"]["notes"]) == 500


def test_model_output_tokens_are_hard_capped_from_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("WOTAGENT_MAX_OUTPUT_TOKENS", "768")

    model = _build_llm(enable_thinking=False)

    assert model.max_tokens == 768
