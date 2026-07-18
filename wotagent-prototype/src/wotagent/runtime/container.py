from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ..agent_graph import (
    AgentGraphDependencies,
    CONDITIONAL_PLAN_REVIEW_SCHEMA,
    DecisionModeTaskPlanner,
    LangChainResponseComposer,
    LangChainTaskPlanner,
    StructuredConditionalPlanReviewer,
    build_agent_graph,
)
from ..agentic_config import AgenticSettings
from ..agents import (
    AGENT_SPECS,
    build_health_react_agent,
    build_run_supervisor_agent,
)
from ..conversation import ConversationStore
from ..core import create_wot_pipeline
from ..domains.eldercare import (
    ELDERCARE_PLAN_SCHEMA,
    EldercareChatAgent,
    EldercareCommandPlanner,
    EldercareRuntime,
    EldercareStore,
    ScopedHealthMCPClient,
)
from ..domains.eldercare.strategies import (
    GOAL_EVALUATION_SCHEMA,
    GOAL_PLAN_SCHEMA,
    HEALTH_DECISION_SCHEMA,
    StructuredGoalEvaluator,
    StructuredGoalPlanner,
    StructuredHealthDecisionMaker,
)
from ..gateway import MCPDeviceGateway
from ..logging import configure_logging
from ..orchestration import TaskOrchestrator
from ..policy import PolicyEngine
from ..prompting import profile_for
from ..telemetry import InMemoryTraceStore


DOMAIN_GRAPH_AGENTS = (
    "environment_agent",
    "security_agent",
    "energy_agent",
    "housekeeping_agent",
    "device_ops_agent",
)


@dataclass
class RuntimeContainer:
    pipeline: Any
    gateway: MCPDeviceGateway
    trace_store: InMemoryTraceStore
    conversation_store: ConversationStore
    eldercare_store: EldercareStore
    settings: AgenticSettings
    run_supervisor_agent: Any
    eldercare_runtime: EldercareRuntime
    eldercare_chat_agent: EldercareChatAgent
    health_react_agent: Any
    health_gateway: ScopedHealthMCPClient
    conditional_plan_review_runnable: Any

    def build_graph(
        self,
        checkpointer: Any,
        *,
        agent_id: str = "environment_agent",
    ):
        allowed_scopes = set(AGENT_SPECS[agent_id].mcp_scopes)
        gateway = MCPDeviceGateway(allowed_scopes=allowed_scopes)
        policy = PolicyEngine()
        dependencies = AgentGraphDependencies(
            planner=DecisionModeTaskPlanner(
                LangChainTaskPlanner(
                    self.pipeline.planner,
                    trace_store=self.trace_store,
                    capability_provider=gateway.capabilities,
                    thinking_runnable=self.pipeline.thinking_planner,
                    agent_profile=profile_for(agent_id),
                    plan_reviewer=StructuredConditionalPlanReviewer(
                        self.conditional_plan_review_runnable,
                        trace_store=self.trace_store,
                    ),
                ),
                mode=self.settings.decision_mode,
                trace_store=self.trace_store,
            ),
            policy=policy,
            orchestrator=TaskOrchestrator(gateway, policy),
            responder=LangChainResponseComposer(
                self.pipeline.response_model,
                trace_store=self.trace_store,
                thinking_runnable=self.pipeline.thinking_response_model,
            ),
            max_agent_iterations=self.settings.device_max_iterations,
            max_tool_calls=self.settings.max_tool_calls,
            trace_store=self.trace_store,
        )
        return build_agent_graph(dependencies, checkpointer=checkpointer)

    def build_domain_graphs(self, checkpointer: Any) -> dict[str, Any]:
        return {
            agent_id: self.build_graph(checkpointer, agent_id=agent_id)
            for agent_id in DOMAIN_GRAPH_AGENTS
        }


def create_runtime_container(
    *,
    enable_thinking: bool,
    enable_streaming: bool,
) -> RuntimeContainer:
    configure_logging()
    pipeline = create_wot_pipeline(
        enable_thinking=enable_thinking,
        reasoning_effort=os.getenv("REASONING_EFFORT", "high"),
        enable_streaming=enable_streaming,
    )
    gateway = MCPDeviceGateway()
    trace_store = InMemoryTraceStore()
    conversation_store = ConversationStore(os.getenv("WOTAGENT_CONVERSATION_DB"))
    eldercare_store = EldercareStore(os.getenv("ELDERCARE_DB"))
    eldercare_store.seed_demo()
    settings = AgenticSettings.from_env()

    goal_plan_runnable = pipeline.response_model.bind(
        tools=[GOAL_PLAN_SCHEMA],
        tool_choice={
            "type": "function",
            "function": {"name": "generate_goal_plan"},
        },
    )
    goal_evaluation_runnable = pipeline.response_model.bind(
        tools=[GOAL_EVALUATION_SCHEMA],
        tool_choice={
            "type": "function",
            "function": {"name": "evaluate_goal"},
        },
    )
    health_decision_runnable = pipeline.response_model.bind(
        tools=[HEALTH_DECISION_SCHEMA],
        tool_choice={
            "type": "function",
            "function": {"name": "decide_health_step"},
        },
    )
    conditional_plan_review_runnable = pipeline.response_model.bind(
        tools=[CONDITIONAL_PLAN_REVIEW_SCHEMA],
        tool_choice={
            "type": "function",
            "function": {"name": "review_conditional_plan"},
        },
    )
    run_supervisor_agent = build_run_supervisor_agent(
        pipeline.response_model,
        AGENT_SPECS,
    )
    eldercare_runtime = EldercareRuntime(
        eldercare_store,
        trace_store=trace_store,
        environment_gateway=gateway,
        settings=settings,
        goal_planner=StructuredGoalPlanner(
            goal_plan_runnable,
            timeout_seconds=settings.llm_timeout_seconds,
            trace_store=trace_store,
        ),
        goal_evaluator=StructuredGoalEvaluator(
            goal_evaluation_runnable,
            timeout_seconds=settings.llm_timeout_seconds,
            trace_store=trace_store,
        ),
        health_decision_maker=StructuredHealthDecisionMaker(
            health_decision_runnable,
            timeout_seconds=settings.llm_timeout_seconds,
            trace_store=trace_store,
        ),
    )
    eldercare_command_runnable = pipeline.response_model.bind(
        tools=[ELDERCARE_PLAN_SCHEMA],
        tool_choice={
            "type": "function",
            "function": {"name": "generate_eldercare_plan"},
        },
    )
    eldercare_chat_agent = EldercareChatAgent(
        eldercare_runtime,
        EldercareCommandPlanner(
            eldercare_command_runnable,
            trace_store=trace_store,
            thinking_runnable=pipeline.thinking_planner,
        ),
        trace_store=trace_store,
    )
    health_gateway = ScopedHealthMCPClient(
        eldercare_store,
        require_scope=True,
    )
    health_react_agent = build_health_react_agent(
        pipeline.response_model,
        health_gateway,
    )
    return RuntimeContainer(
        pipeline=pipeline,
        gateway=gateway,
        trace_store=trace_store,
        conversation_store=conversation_store,
        eldercare_store=eldercare_store,
        settings=settings,
        run_supervisor_agent=run_supervisor_agent,
        eldercare_runtime=eldercare_runtime,
        eldercare_chat_agent=eldercare_chat_agent,
        health_react_agent=health_react_agent,
        health_gateway=health_gateway,
        conditional_plan_review_runnable=conditional_plan_review_runnable,
    )


__all__ = [
    "DOMAIN_GRAPH_AGENTS",
    "RuntimeContainer",
    "create_runtime_container",
]
