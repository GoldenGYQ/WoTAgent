"""Core module -- agent pipeline, session management, and conversation state."""

from __future__ import annotations

from .handlers import execute_plan_directly, invoke_agent, invoke_agent_stream
from .models import AgentContext, PipelinePlan
from .pipeline import AgentPipeline, create_wot_agent, create_wot_pipeline
from .session import AgentSession, SessionInfo, SessionManager, get_session_manager
from .state import ConversationState

__all__ = [
    "AgentContext",
    "AgentPipeline",
    "PipelinePlan",
    "ConversationState",
    "create_wot_agent",
    "create_wot_pipeline",
    "execute_plan_directly",
    "invoke_agent",
    "invoke_agent_stream",
    "AgentSession",
    "SessionManager",
    "SessionInfo",
    "get_session_manager",
]
