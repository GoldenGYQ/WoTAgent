"""Platform boundary for resolving domain mentions to canonical targets."""

from __future__ import annotations

from typing import Protocol

from ..orchestration.run_models import TaskTarget


class EntityResolutionError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class EntityResolver(Protocol):
    def resolve(
        self,
        *,
        target_type: str,
        mention: str | None,
        text: str,
    ) -> TaskTarget:
        ...


__all__ = ["EntityResolutionError", "EntityResolver"]
