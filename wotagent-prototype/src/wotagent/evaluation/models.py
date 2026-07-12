"""Framework-neutral benchmark cases."""

from __future__ import annotations

from typing import Any

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
