"""Framework-neutral benchmark cases."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EvalAction(BaseModel):
    device_id: str
    action: str
    input: dict[str, Any] = Field(default_factory=dict)

    def key(self) -> str:
        import json
        return f"{self.device_id}|{self.action}|{json.dumps(self.input, sort_keys=True)}"


class EvalCase(BaseModel):
    case_id: str
    source: str
    category: str
    instruction: str
    expected_intent: str = "control"
    expected_actions: list[EvalAction] = Field(default_factory=list)


class LoopEvalTurn(BaseModel):
    message: str
    role: str = "operator"


class LoopFault(BaseModel):
    type: Literal[
        "verification_miss",
        "tool_failure",
        "persistent_failure",
        "partial_failure",
        "user_correction",
        "clarification",
    ]
    fail_iterations: list[int] = Field(default_factory=list)
    target_step_ids: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class LoopExpectation(BaseModel):
    min_iterations: int = Field(default=2, ge=2)
    max_iterations: int = Field(default=3, ge=2)
    final_status: str
    required_events: list[str] = Field(default_factory=list)
    expected_actions: list[EvalAction] = Field(default_factory=list)


class LoopEvalCase(BaseModel):
    case_id: str
    category: str
    description: str
    support: Literal["implemented", "target"]
    turns: list[LoopEvalTurn]
    fault: LoopFault
    expected: LoopExpectation
