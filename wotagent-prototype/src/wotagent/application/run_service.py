"""Application service for Run creation and background execution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException

from ..conversation import ConversationStore
from ..domain import DeploymentProfile, DeploymentProfileRegistry
from ..identity import (
    DEFAULT_PRINCIPAL_DIRECTORY,
    PrincipalAuthenticationError,
    PrincipalDirectory,
    PrincipalNotAssigned,
    TaskPrincipal,
)
from ..orchestration import RunCoordinator, RunRecord, RunStore


class RunService:
    def __init__(
        self,
        conversation_store: ConversationStore,
        run_store: RunStore,
        coordinator_factory: Callable[[dict[str, Any]], RunCoordinator],
        thinking_supported: Callable[[], bool] | None = None,
        deployment_profiles: DeploymentProfileRegistry | None = None,
        principal_directory: PrincipalDirectory | None = None,
    ) -> None:
        self.conversation_store = conversation_store
        self.run_store = run_store
        self.coordinator_factory = coordinator_factory
        self.thinking_supported = thinking_supported or (lambda: True)
        self.deployment_profiles = (
            deployment_profiles or DeploymentProfileRegistry.load()
        )
        self.principal_directory = (
            principal_directory or DEFAULT_PRINCIPAL_DIRECTORY
        )

    async def create(
        self,
        *,
        message: str,
        conversation_id: str | None,
        role: str,
        user_id: str,
        thinking: bool,
        profile_id: str | None = None,
        phone_mode: bool = False,
        auth_token: str | None = None,
        background_tasks: BackgroundTasks,
    ) -> RunRecord:
        if thinking and not self.thinking_supported():
            raise HTTPException(
                status_code=409,
                detail="thinking mode is not available on this server",
            )
        profile = self._require_profile(profile_id)
        principal = self._resolve_principal(
            user_id,
            profile.domain_id,
            fallback_role=role,
            auth_token=auth_token,
        )
        role = principal.roles[0]
        conversation_id = self._resolve_conversation(
            conversation_id,
            user_id=user_id,
            role=role,
        )
        await self._assert_conversation_profile(
            conversation_id,
            profile.profile_id,
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
            role=role,
            user_id=user_id,
            principal=principal,
            thinking=thinking,
            domain_id=profile.domain_id,
            profile_id=profile.profile_id,
            site_id=profile.site_id,
            source_context=_interaction_source_context(phone_mode=phone_mode),
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

    async def create_event_run(
        self,
        *,
        message: str,
        domain_id: str,
        source_event_id: str,
        source_context: dict[str, object],
        user_id: str,
        role: str = "operator",
        on_created: Callable[[RunRecord], None] | None = None,
    ) -> RunRecord:
        """Create and execute an isolated Run for one trusted domain event."""
        conversation_id = f"conv_event_{source_event_id}"
        conversation_id = self._resolve_conversation(
            conversation_id,
            user_id=user_id,
            role=role,
        )
        profile = self._profile_for_domain(domain_id)
        principal = self._resolve_principal(
            user_id,
            profile.domain_id,
            fallback_role=role,
        )
        role = principal.roles[0]
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
            role=role,
            user_id=user_id,
            principal=principal,
            thinking=False,
            source="monitor",
            domain_id=domain_id,
            profile_id=profile.profile_id,
            site_id=profile.site_id,
            source_event_id=source_event_id,
            source_context=source_context,
            status="accepted",
        ))
        if on_created is not None:
            on_created(run)
        await self.run_store.record_event(
            run_id,
            "run.source.received",
            node="domain_event_consumer",
            data={
                "source": "monitor",
                "domain_id": domain_id,
                "source_event_id": source_event_id,
                "context": source_context,
            },
        )
        await self.execute(
            run_id=run_id,
            message=message,
            role=role,
            user_id=user_id,
            thinking=False,
        )
        return await self.require_run(run_id)

    async def create_domain_scenario_run(
        self,
        *,
        message: str,
        domain_id: str,
        scenario: str,
        source_context: dict[str, object],
        user_id: str,
        background_tasks: BackgroundTasks,
    ) -> RunRecord:
        """Create a user-visible Run from a trusted domain scenario."""
        profile = self._profile_for_domain(domain_id)
        principal = self.principal_directory.resolve(user_id, domain_id)
        role = principal.roles[0]
        conversation_id = self._resolve_conversation(
            None,
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
            role=role,
            user_id=user_id,
            principal=principal,
            thinking=False,
            source="system",
            domain_id=domain_id,
            profile_id=profile.profile_id,
            site_id=profile.site_id,
            source_context={
                "type": "domain_scenario",
                "scenario": scenario,
                **source_context,
            },
            status="accepted",
        ))
        await self.run_store.record_event(
            run_id,
            "run.source.received",
            node="domain_scenario",
            data={
                "source": "domain_scenario",
                "domain_id": domain_id,
                "scenario": scenario,
                "context": source_context,
            },
        )
        background_tasks.add_task(
            self.execute,
            run_id=run_id,
            message=message,
            role=role,
            user_id=user_id,
            thinking=False,
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
        context = self._build_context(run)
        try:
            coordinator = self.coordinator_factory(context)
            completed = await coordinator.execute(
                run_id=run_id,
                conversation_id=run.conversation_id,
                turn_id=run.turn_id,
                message=message,
                context=context,
            )
            completed_tasks = await self.run_store.tasks_for_run(run_id)
            self.conversation_store.finish_turn(
                run_id,
                response=completed.final_response,
                status=completed.status,
                result={
                    **completed.model_dump(mode="json"),
                    "tasks": [
                        task.model_dump(mode="json")
                        for task in completed_tasks
                    ],
                },
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

    def _build_context(self, run: RunRecord) -> dict[str, Any]:
        profile = self._require_profile(run.profile_id)
        return {
            "role": run.role,
            "user_id": run.user_id,
            "principal": (
                run.principal.model_dump(mode="json")
                if run.principal
                else None
            ),
            "thinking": run.thinking,
            "history": self.conversation_store.context(
                run.conversation_id,
                exclude_run_id=run.run_id,
            ),
            "runtime": self.conversation_store.runtime_context(
                run.conversation_id,
                current_role=run.role,
                exclude_run_id=run.run_id,
            ),
            "source": {
                "type": run.source,
                "domain_id": run.domain_id,
                "event_id": run.source_event_id,
                **run.source_context,
            },
            "deployment": profile.model_dump(mode="json"),
        }

    def _resolve_principal(
        self,
        principal_id: str,
        domain_id: str,
        *,
        fallback_role: str,
        auth_token: str | None = None,
    ) -> TaskPrincipal:
        try:
            resolver = getattr(
                self.principal_directory,
                "resolve_for_request",
                None,
            )
            if callable(resolver):
                return resolver(
                    principal_id=principal_id,
                    domain_id=domain_id,
                    token=auth_token,
                    fallback_role=fallback_role,
                )
            return self.principal_directory.resolve(
                principal_id,
                domain_id,
                fallback_role=fallback_role,
            )
        except PrincipalAuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except PrincipalNotAssigned as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    async def confirm(
        self,
        *,
        run_id: str,
        task_id: str,
        approved: bool,
    ) -> RunRecord:
        run = await self.require_run(run_id)
        if run.status != "waiting_confirmation":
            raise HTTPException(
                status_code=409,
                detail="run is not waiting for confirmation",
            )
        context = self._build_context(run)
        coordinator = self.coordinator_factory(context)
        try:
            completed = await coordinator.confirm(
                run_id=run_id,
                task_id=task_id,
                approved=approved,
                message=run.root_goal,
                context=context,
            )
        except LookupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        self.conversation_store.finish_turn(
            run_id,
            response=completed.final_response,
            status=completed.status,
            result={
                **completed.model_dump(mode="json"),
                "tasks": [
                    task.model_dump(mode="json")
                    for task in await self.run_store.tasks_for_run(run_id)
                ],
            },
        )
        return completed

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

    def _require_profile(
        self,
        profile_id: str | None,
    ) -> DeploymentProfile:
        try:
            return self.deployment_profiles.require(profile_id)
        except LookupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _profile_for_domain(self, domain_id: str) -> DeploymentProfile:
        matches = [
            profile
            for profile in self.deployment_profiles.list()
            if profile.domain_id == domain_id
        ]
        if len(matches) != 1:
            raise HTTPException(
                status_code=422,
                detail=f"domain '{domain_id}' has no unique deployment profile",
            )
        return matches[0]

    async def _assert_conversation_profile(
        self,
        conversation_id: str,
        profile_id: str,
    ) -> None:
        turns = self.conversation_store.turns(conversation_id)
        for turn in turns:
            run_id = str(turn.get("run_id", ""))
            if not run_id:
                continue
            previous = await self.run_store.get_run(run_id)
            if (
                previous is not None
                and previous.profile_id is not None
                and previous.profile_id != profile_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "conversation belongs to deployment profile "
                        f"'{previous.profile_id}'; create a new conversation "
                        f"before switching to '{profile_id}'"
                    ),
                )


def _interaction_source_context(*, phone_mode: bool) -> dict[str, Any]:
    if not phone_mode:
        return {}
    return {
        "interaction_mode": {
            "channel": "phone",
            "voice_reply": True,
        },
        "response_style": {
            "spoken": True,
            "concise": True,
            "tone": "warm_phone_call",
        },
    }


def _fail_run(run: RunRecord, error: str) -> None:
    run.status = "failed"
    run.error = error
    run.final_response = error


__all__ = ["RunService"]
