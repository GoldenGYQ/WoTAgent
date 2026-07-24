from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    DEFAULT_AGENT_REGISTRY,
    AgentRegistry,
    AgentFindingPayload,
    build_care_react_agent,
    build_health_react_agent,
    build_run_supervisor_agent,
)
from ..conversation import ConversationStore
from ..core import create_llm_runtime
from ..domains.eldercare import (
    ELDERCARE_PLAN_SCHEMA,
    EldercareChatAgent,
    EldercareCommandPlanner,
    EldercareRuntime,
    EldercareStore,
    ScopedHealthMCPClient,
    ScopedCareMCPClient,
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


DOMAIN_GRAPH_AGENTS = DEFAULT_AGENT_REGISTRY.ids(
    runtime="langgraph_subgraph"
)

HEALTH_FINDING_REPAIR_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "repair_health_finding",
        "description": (
            "Convert observed Health Agent facts into AgentFindingPayload"
        ),
        "parameters": AgentFindingPayload.model_json_schema(),
    },
}


@dataclass
class RuntimeContainer:
    models: Any
    gateway: MCPDeviceGateway
    trace_store: InMemoryTraceStore
    conversation_store: ConversationStore
    eldercare_store: EldercareStore
    settings: AgenticSettings
    run_supervisor_agent: Any
    eldercare_runtime: EldercareRuntime
    eldercare_chat_agent: EldercareChatAgent
    care_react_agent: Any
    care_gateway: ScopedCareMCPClient
    health_react_agent: Any
    health_finding_repair_runnable: Any
    health_gateway: ScopedHealthMCPClient
    conditional_plan_review_runnable: Any
    agent_registry: AgentRegistry = field(
        default_factory=lambda: DEFAULT_AGENT_REGISTRY,
    )
    _supervisor_cache: dict[tuple[str, ...], Any] = field(
        default_factory=dict,
    )

    def build_graph(
        self,
        checkpointer: Any,
        *,
        agent_id: str = "environment_agent",
    ):
        allowed_scopes = set(
            self.agent_registry.require(agent_id).mcp_scopes
        )
        gateway = MCPDeviceGateway(allowed_scopes=allowed_scopes)
        policy = PolicyEngine()
        dependencies = AgentGraphDependencies(
            planner=DecisionModeTaskPlanner(
                LangChainTaskPlanner(
                    self.models.planner,
                    trace_store=self.trace_store,
                    capability_provider=gateway.capabilities,
                    thinking_runnable=self.models.thinking_planner,
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
                self.models.response_model,
                trace_store=self.trace_store,
                thinking_runnable=self.models.thinking_response_model,
            ),
            max_agent_iterations=self.settings.device_max_iterations,
            max_tool_calls=self.settings.max_tool_calls,
            trace_store=self.trace_store,
        )
        return build_agent_graph(dependencies, checkpointer=checkpointer)

    def build_domain_graphs(self, checkpointer: Any) -> dict[str, Any]:
        return {
            agent_id: self.build_graph(checkpointer, agent_id=agent_id)
            for agent_id in self.agent_registry.ids(
                runtime="langgraph_subgraph"
            )
        }

    def supervisor_for(self, enabled_agents: list[str]) -> Any:
        if not enabled_agents:
            return self.run_supervisor_agent
        key = tuple(sorted(set(enabled_agents)))
        cached = self._supervisor_cache.get(key)
        if cached is not None:
            return cached
        supervisor = build_run_supervisor_agent(
            self.models.response_model,
            self.agent_registry.select(key),
        )
        self._supervisor_cache[key] = supervisor
        return supervisor


def create_runtime_container(
    *,
    enable_thinking: bool,
    enable_streaming: bool,
    temperature: float | None = None,
) -> RuntimeContainer:
    configure_logging()
    models = create_llm_runtime(
        enable_thinking=enable_thinking,
        reasoning_effort=os.getenv("REASONING_EFFORT", "high"),
        enable_streaming=enable_streaming,
        temperature=0.7 if temperature is None else temperature,
    )
    gateway = MCPDeviceGateway()
    trace_store = InMemoryTraceStore()
    conversation_store = ConversationStore(os.getenv("WOTAGENT_CONVERSATION_DB"))
    eldercare_store = EldercareStore(os.getenv("ELDERCARE_DB"))
    eldercare_store.seed_demo()
    settings = AgenticSettings.from_env()

    goal_plan_runnable = models.response_model.bind(
        tools=[GOAL_PLAN_SCHEMA],
        tool_choice={
            "type": "function",
            "function": {"name": "generate_goal_plan"},
        },
    )
    goal_evaluation_runnable = models.response_model.bind(
        tools=[GOAL_EVALUATION_SCHEMA],
        tool_choice={
            "type": "function",
            "function": {"name": "evaluate_goal"},
        },
    )
    health_decision_runnable = models.response_model.bind(
        tools=[HEALTH_DECISION_SCHEMA],
        tool_choice={
            "type": "function",
            "function": {"name": "decide_health_step"},
        },
    )
    conditional_plan_review_runnable = models.response_model.bind(
        tools=[CONDITIONAL_PLAN_REVIEW_SCHEMA],
        tool_choice={
            "type": "function",
            "function": {"name": "review_conditional_plan"},
        },
    )
    run_supervisor_agent = build_run_supervisor_agent(
        models.response_model,
        DEFAULT_AGENT_REGISTRY.as_mapping(),
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
    eldercare_command_runnable = models.response_model.bind(
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
            thinking_runnable=models.thinking_planner,
        ),
        trace_store=trace_store,
    )
    health_gateway = ScopedHealthMCPClient(
        eldercare_store,
        require_scope=True,
    )
    health_react_agent = build_health_react_agent(
        models.response_model,
        health_gateway,
        max_iterations=settings.health_max_iterations,
        max_tool_calls=settings.max_tool_calls,
    )
    care_gateway = ScopedCareMCPClient(
        eldercare_store,
        require_scope=True,
    )
    care_react_agent = build_care_react_agent(
        models.response_model,
        care_gateway,
        max_iterations=DEFAULT_AGENT_REGISTRY.require(
            "care_agent"
        ).max_iterations,
        max_tool_calls=min(settings.max_tool_calls, 4),
    )
    health_finding_repair_runnable = models.response_model.bind(
        tools=[HEALTH_FINDING_REPAIR_SCHEMA],
        tool_choice={
            "type": "function",
            "function": {"name": "repair_health_finding"},
        },
    )
    return RuntimeContainer(
        models=models,
        gateway=gateway,
        trace_store=trace_store,
        conversation_store=conversation_store,
        eldercare_store=eldercare_store,
        settings=settings,
        run_supervisor_agent=run_supervisor_agent,
        eldercare_runtime=eldercare_runtime,
        eldercare_chat_agent=eldercare_chat_agent,
        care_react_agent=care_react_agent,
        care_gateway=care_gateway,
        health_react_agent=health_react_agent,
        health_finding_repair_runnable=health_finding_repair_runnable,
        health_gateway=health_gateway,
        conditional_plan_review_runnable=conditional_plan_review_runnable,
        agent_registry=DEFAULT_AGENT_REGISTRY,
    )


__all__ = [
    "DOMAIN_GRAPH_AGENTS",
    "RuntimeContainer",
    "create_runtime_container",
]
