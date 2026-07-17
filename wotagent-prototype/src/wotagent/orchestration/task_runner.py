from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from langgraph.types import Command

from ..application import TaskCreateV2Response
from ..supervisor import RouteDecision, select_rule_supervisor_route


@dataclass(frozen=True)
class TaskRuntimeAccess:
    get_pipeline: Callable[[], Any]
    get_trace_store: Callable[[], Any]
    get_conversation_store: Callable[[], Any]
    get_settings: Callable[[], Any]
    get_supervisor_router: Callable[[], Any]
    get_eldercare_store: Callable[[], Any]
    get_eldercare_chat_agent: Callable[[], Any]
    get_primary_graph: Callable[[], Any]
    get_domain_graphs: Callable[[], dict[str, Any]]
    task_graphs: dict[str, Any]


class TaskRunner:
    def __init__(self, access: TaskRuntimeAccess) -> None:
        self.access = access

    async def create(
        self,
        *,
        message: str,
        conversation_id: str | None,
        session_id: str | None,
        role: str,
        user_id: str,
        thinking: bool,
        background_tasks: BackgroundTasks,
    ) -> TaskCreateV2Response:
        if thinking and self.access.get_pipeline().thinking_planner is None:
            raise HTTPException(
                status_code=409,
                detail="thinking mode is not available on this server",
            )
        store = self.access.get_conversation_store()
        resolved_conversation_id = conversation_id or session_id
        if resolved_conversation_id is None:
            conversation = store.create(user_id=user_id, role=role)
            resolved_conversation_id = conversation["conversation_id"]
        elif store.get(resolved_conversation_id) is None:
            store.create(
                conversation_id=resolved_conversation_id,
                user_id=user_id,
                role=role,
            )

        request_id = f"req_{uuid.uuid4().hex[:12]}"
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        turn = store.start_turn(
            conversation_id=resolved_conversation_id,
            task_id=task_id,
            user_message=message,
            role=role,
        )
        await self.access.get_trace_store().create(
            task_id=task_id,
            session_id=resolved_conversation_id,
            request_id=request_id,
        )
        background_tasks.add_task(
            self.run,
            task_id=task_id,
            request_id=request_id,
            conversation_id=resolved_conversation_id,
            user_id=user_id,
            role=role,
            message=message,
            thinking=thinking,
        )
        return TaskCreateV2Response(
            task_id=task_id,
            turn_id=turn["turn_id"],
            conversation_id=resolved_conversation_id,
            session_id=resolved_conversation_id,
            status="accepted",
            response="",
            thinking=thinking,
        )

    async def confirm(
        self,
        *,
        task_id: str,
        approved: bool,
    ) -> TaskCreateV2Response:
        trace_store = self.access.get_trace_store()
        conversation_store = self.access.get_conversation_store()
        record = await trace_store.get(task_id)
        if record is None:
            turn = conversation_store.turn_for_task(task_id)
            if turn is None:
                raise HTTPException(status_code=404, detail="task not found")
            if turn["status"] != "waiting_confirmation":
                raise HTTPException(
                    status_code=409,
                    detail="task is not waiting for confirmation",
                )
            conversation_id = turn["conversation_id"]
            await trace_store.create(
                task_id=task_id,
                session_id=conversation_id,
                request_id=f"req_resume_{uuid.uuid4().hex[:8]}",
            )
            await trace_store.set_status(task_id, "waiting_confirmation")
        else:
            conversation_id = record.session_id

        graph = self.access.task_graphs.get(
            task_id,
            self.access.get_primary_graph(),
        )
        config = {"configurable": {"thread_id": task_id}}
        snapshot = await graph.aget_state(config)
        if not snapshot.values or not snapshot.interrupts:
            raise HTTPException(
                status_code=409,
                detail="task is not waiting for confirmation",
            )
        result = await graph.ainvoke(
            Command(resume={"approved": approved}),
            config=config,
        )
        response = self.task_response(result, conversation_id)
        await self.finish(response)
        return response

    async def run(
        self,
        *,
        task_id: str,
        request_id: str,
        conversation_id: str,
        user_id: str,
        role: str,
        message: str,
        thinking: bool,
    ) -> None:
        trace_store = self.access.get_trace_store()
        conversation_store = self.access.get_conversation_store()
        await trace_store.set_status(task_id, "running")
        await trace_store.record(
            task_id=task_id,
            type="task.started",
            node="api",
        )
        try:
            history = conversation_store.context(
                conversation_id,
                exclude_task_id=task_id,
            )
            runtime_context = conversation_store.runtime_context(
                conversation_id,
                current_role=role,
                exclude_task_id=task_id,
            )
            await trace_store.record(
                task_id=task_id,
                type="context.built",
                node="context",
                data={
                    "conversation_id": conversation_id,
                    "history_messages": len(history),
                    "history_characters": sum(
                        len(item["content"]) for item in history
                    ),
                    "current_role": role,
                    "has_pending_goal": (
                        runtime_context["pending_goal"] is not None
                    ),
                    "thinking": thinking,
                },
            )
            attempted_agents: list[str] = []
            previous_outcome: dict[str, Any] | None = None
            settings = self.access.get_settings()
            for route_iteration in range(1, settings.supervisor_max_routes + 1):
                if settings.decision_mode == "rule":
                    route = select_rule_supervisor_route(
                        message,
                        history,
                        self.access.get_eldercare_store().list_residents(),
                        excluded_agents=set(attempted_agents),
                    )
                else:
                    route = await self.access.get_supervisor_router().route(
                        task_id=task_id,
                        message=message,
                        history=history,
                        residents=(
                            self.access.get_eldercare_store().list_residents()
                        ),
                        runtime_context=runtime_context,
                        attempted_agents=attempted_agents,
                        previous_outcome=previous_outcome,
                    )
                await self.record_supervisor_route(
                    task_id,
                    route,
                    route_iteration,
                )
                if route.action == "clarify":
                    await self.finish(self.clarification_response(
                        task_id,
                        conversation_id,
                        thinking,
                        route,
                    ))
                    return

                response, outcome = await self.execute_supervisor_route(
                    task_id=task_id,
                    request_id=request_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    role=role,
                    original_message=message,
                    thinking=thinking,
                    history=history,
                    runtime_context=runtime_context,
                    route=route,
                )
                if response.status == "waiting_confirmation":
                    await trace_store.record(
                        task_id=task_id,
                        type="task.waiting_confirmation",
                        node="confirmation",
                        data={"confirmation": response.confirmation or {}},
                    )
                    await trace_store.set_status(
                        task_id,
                        "waiting_confirmation",
                    )
                    conversation_store.finish_turn(
                        task_id,
                        response=response.response,
                        status="waiting_confirmation",
                        result=response.model_dump(mode="json"),
                    )
                    return
                if (
                    not self.should_reroute(outcome)
                    or route_iteration >= settings.supervisor_max_routes
                ):
                    await self.finish(response)
                    return
                attempted_agents.append(str(route.agent_id))
                previous_outcome = outcome
                await trace_store.record(
                    task_id=task_id,
                    type="agent.delegation.returned",
                    node=str(route.agent_id),
                    iteration=route_iteration,
                    data={
                        "status": response.status,
                        "reason": (
                            "specialist returned structured clarify outcome"
                        ),
                        "outcome": outcome,
                    },
                )
        except Exception as exc:
            await trace_store.record(
                task_id=task_id,
                type="task.failed",
                node="api",
                data={"error": str(exc)},
            )
            await trace_store.finish(
                task_id,
                status="failed",
                result={"error": str(exc)},
            )
            conversation_store.finish_turn(
                task_id,
                response=str(exc),
                status="failed",
                result={"status": "failed", "error": str(exc)},
            )

    async def record_supervisor_route(
        self,
        task_id: str,
        route: RouteDecision,
        iteration: int,
    ) -> None:
        await self.access.get_trace_store().record(
            task_id=task_id,
            type=(
                "agent.reroute.selected"
                if iteration > 1
                else "agent.delegation.created"
            ),
            node="butler_supervisor",
            iteration=iteration,
            data={
                "agent_id": route.agent_id,
                "runtime": route.runtime if route.agent_id else None,
                "resolved_request": route.resolved_request,
                "resident_id": route.resident_id,
                "confidence": route.confidence,
                "reason": route.reason,
                "action": route.action,
                "execution_mode": route.execution_mode,
            },
        )

    async def execute_supervisor_route(
        self,
        *,
        task_id: str,
        request_id: str,
        conversation_id: str,
        user_id: str,
        role: str,
        original_message: str,
        thinking: bool,
        history: list[dict[str, Any]],
        runtime_context: dict[str, Any],
        route: RouteDecision,
    ) -> tuple[TaskCreateV2Response, dict[str, Any]]:
        delegated_message = route.resolved_request or original_message
        if route.runtime == "eldercare":
            eldercare_result = await self.access.get_eldercare_chat_agent().run(
                task_id=task_id,
                message=delegated_message,
                history=history,
                role=role,
                thinking=thinking,
                routed_agent=route.agent_id,
                resident_id=route.resident_id,
            )
            if eldercare_result.get("plan") is not None:
                await self.access.get_trace_store().record(
                    task_id=task_id,
                    type="agent.plan.completed",
                    node="eldercare_planner",
                    data={"plan": eldercare_result["plan"]},
                )
            response = TaskCreateV2Response(
                task_id=task_id,
                turn_id=(
                    self.access.get_conversation_store().turn_for_task(task_id)
                    or {}
                ).get("turn_id"),
                conversation_id=conversation_id,
                session_id=conversation_id,
                status=str(eldercare_result["status"]),
                response=str(eldercare_result["response"]),
                plan=eldercare_result.get("plan"),
                thinking=thinking,
            )
            return response, {
                "agent_id": route.agent_id,
                "status": response.status,
                "plan": eldercare_result.get("plan"),
                "response": response.response,
            }

        graph = self.selected_domain_graph(str(route.agent_id))
        self.access.task_graphs[task_id] = graph
        result = await graph.ainvoke(
            {
                "request_id": request_id,
                "task_id": task_id,
                "session_id": conversation_id,
                "user_id": user_id,
                "role": role,
                "message": delegated_message,
                "thinking": thinking,
                "conversation_history": history,
                "context": {
                    "runtime": runtime_context,
                    "delegated_agent": route.agent_id,
                    "original_message": original_message,
                    "supervisor_reason": route.reason,
                    "execution_mode": route.execution_mode,
                },
            },
            config={"configurable": {"thread_id": task_id}},
        )
        response = self.task_response(result, conversation_id)
        return response, {
            "agent_id": route.agent_id,
            "status": response.status,
            "plan": (
                _model_dump(result.get("plan"))
                if result.get("plan") is not None
                else None
            ),
            "phase": result.get("phase"),
            "error": result.get("error", ""),
            "response": response.response,
        }

    def clarification_response(
        self,
        task_id: str,
        conversation_id: str,
        thinking: bool,
        route: RouteDecision,
    ) -> TaskCreateV2Response:
        turn = self.access.get_conversation_store().turn_for_task(task_id) or {}
        return TaskCreateV2Response(
            task_id=task_id,
            turn_id=turn.get("turn_id"),
            conversation_id=conversation_id,
            session_id=conversation_id,
            status="responded",
            response=route.clarification,
            thinking=thinking,
        )

    def selected_domain_graph(self, agent_id: str):
        domain_graphs = self.access.get_domain_graphs()
        primary_graph = self.access.get_primary_graph()
        environment_graph = domain_graphs.get("environment_agent")
        if (
            environment_graph is not None
            and primary_graph is not environment_graph
        ):
            return primary_graph
        return domain_graphs.get(agent_id, primary_graph)

    def task_response(
        self,
        result: dict[str, Any],
        session_id: str,
    ) -> TaskCreateV2Response:
        plan = result.get("plan")
        trace = result.get("trace")
        interrupts = result.get("__interrupt__", [])
        confirmation = interrupts[0].value if interrupts else None
        decisions = result.get("policy_decisions", [])
        if result.get("error"):
            status = "failed"
        elif trace is not None:
            status = trace.status.value
        elif confirmation:
            status = "waiting_confirmation"
        elif decisions and all(
            _decision_effect(item) == "deny" for item in decisions
        ):
            status = "denied"
        else:
            status = result.get("phase", "unknown")
        task_id = trace.task_id if trace is not None else result.get("task_id")
        turn = (
            self.access.get_conversation_store().turn_for_task(task_id or "")
            or {}
        )
        return TaskCreateV2Response(
            task_id=task_id,
            turn_id=turn.get("turn_id"),
            conversation_id=session_id,
            session_id=session_id,
            status=status,
            response=result.get("response", "") or (
                "该操作需要确认后才能执行。" if confirmation else ""
            ),
            plan=_model_dump(plan) if plan is not None else None,
            trace=_model_dump(trace) if trace is not None else None,
            confirmation=confirmation,
            policy_decisions=[_model_dump(item) for item in decisions],
            thinking=bool(result.get("thinking", False)),
        )

    async def finish(self, response: TaskCreateV2Response) -> None:
        if response.task_id is None:
            return
        trace_store = self.access.get_trace_store()
        await trace_store.record(
            task_id=response.task_id,
            type=f"task.{response.status}",
            node="api",
            data={"response": response.response},
        )
        payload = response.model_dump(mode="json")
        await trace_store.finish(
            response.task_id,
            status=response.status,
            result=payload,
        )
        self.access.get_conversation_store().finish_turn(
            response.task_id,
            response=response.response,
            status=response.status,
            result=payload,
        )

    @staticmethod
    def should_reroute(outcome: dict[str, Any]) -> bool:
        plan = outcome.get("plan")
        return (
            isinstance(plan, dict)
            and plan.get("intent") == "clarify"
        ) or outcome.get("phase") == "planning_failed"


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value) if isinstance(value, dict) else {}


def _decision_effect(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("effect", ""))
    return str(getattr(value, "effect", ""))


__all__ = ["TaskRunner", "TaskRuntimeAccess"]
