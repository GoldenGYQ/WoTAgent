"""Application service for Run creation and background execution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException

from ..conversation import ConversationStore
from ..orchestration import InMemoryRunStore, RunCoordinator, RunRecord


class RunService:
    def __init__(
        self,
        conversation_store: ConversationStore,
        run_store: InMemoryRunStore,
        coordinator_factory: Callable[[dict[str, Any]], RunCoordinator],
        thinking_supported: Callable[[], bool] | None = None,
    ) -> None:
        self.conversation_store = conversation_store
        self.run_store = run_store
        self.coordinator_factory = coordinator_factory
        self.thinking_supported = thinking_supported or (lambda: True)

    async def create(
        self,
        *,
        message: str,
        conversation_id: str | None,
        role: str,
        user_id: str,
        thinking: bool,
        background_tasks: BackgroundTasks,
    ) -> RunRecord:
        if thinking and not self.thinking_supported():
            raise HTTPException(
                status_code=409,
                detail="thinking mode is not available on this server",
            )
        conversation_id = self._resolve_conversation(
            conversation_id,
            user_id=user_id,
            role=role,
        )
        run_id = f"run_{uuid4().hex[:12]}"
        turn = self.conversation_store.start_turn(
            conversation_id=conversation_id,
            run_id=run_id,
            user_message=message,
            role=role,
        )
        run = await self.run_store.create_run(RunRecord(
            run_id=run_id,
            conversation_id=conversation_id,
            turn_id=turn["turn_id"],
            root_goal=message,
            status="accepted",
        ))
        background_tasks.add_task(
            self.execute,
            run_id=run_id,
            message=message,
            role=role,
            user_id=user_id,
            thinking=thinking,
        )
        return run

    async def execute(
        self,
        *,
        run_id: str,
        message: str,
        role: str,
        user_id: str,
        thinking: bool,
    ) -> None:
        run = await self.require_run(run_id)
        context = {
            "role": role,
            "user_id": user_id,
            "thinking": thinking,
            "history": self.conversation_store.context(
                run.conversation_id,
                exclude_run_id=run_id,
            ),
            "runtime": self.conversation_store.runtime_context(
                run.conversation_id,
                current_role=role,
                exclude_run_id=run_id,
            ),
        }
        try:
            coordinator = self.coordinator_factory(context)
            completed = await coordinator.execute(
                run_id=run_id,
                conversation_id=run.conversation_id,
                turn_id=run.turn_id,
                message=message,
                context=context,
            )
            self.conversation_store.finish_turn(
                run_id,
                response=completed.final_response,
                status=completed.status,
                result=completed.model_dump(mode="json"),
            )
        except Exception as exc:
            failed = await self.run_store.update_run(
                run_id,
                lambda item: _fail_run(item, str(exc)),
            )
            await self.run_store.record_event(
                run_id,
                "run.failed",
                node="run_service",
                data={"error": str(exc)},
            )
            self.conversation_store.finish_turn(
                run_id,
                response=str(exc),
                status="failed",
                result=failed.model_dump(mode="json"),
            )

    async def require_run(self, run_id: str) -> RunRecord:
        run = await self.run_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    def _resolve_conversation(
        self,
        conversation_id: str | None,
        *,
        user_id: str,
        role: str,
    ) -> str:
        if conversation_id is None:
            return str(
                self.conversation_store.create(
                    user_id=user_id,
                    role=role,
                )["conversation_id"]
            )
        if self.conversation_store.get(conversation_id) is None:
            self.conversation_store.create(
                conversation_id=conversation_id,
                user_id=user_id,
                role=role,
            )
        return conversation_id


def _fail_run(run: RunRecord, error: str) -> None:
    run.status = "failed"
    run.final_response = error


__all__ = ["RunService"]
