"""Multi-agent pipeline: Planner → Executor/Observer with conditional routing.

Architecture:

    User message
         │
         ▼
    ┌──────────┐
    │ Planner  │ ── intent analysis → structured plan
    └────┬─────┘
         │
    ┌────┴────────────┐
    │ intent=chat     │ intent=control|query
    ▼                 ▼
┌──────────┐   ┌──────────┐
│Responder │   │ Executor │ ── tool calls (streamed)
│(direct)  │   └────┬─────┘
└──────────┘        │
                    ▼
              ┌──────────┐
              │ Observer │ ── validate → final response
              └──────────┘
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    dynamic_prompt,
)
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from .. import prompts
from ..events import Event, get_bus
from ..memory import ConversationMemory, SessionTranscript
from ..tools import get_all_tools
from .state import ConversationState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context schema — passed to every agent at invocation time
# ---------------------------------------------------------------------------

class AgentContext(BaseModel):
    """Runtime context injected into the agent on every invocation."""
    session_id: str = Field(default="", description="Current session ID")
    user_role: str = Field(default="operator", description="RBAC role")
    user_message: str = Field(default="", description="The raw user message")
    plan_json: str = Field(default="{}", description="Plan from planner (JSON)")
    execution_results: str = Field(default="", description="Executor output for observer")
    perception_context: str = Field(default="", description="Current environment state summary")


# ---------------------------------------------------------------------------
# Pipeline plan model
# ---------------------------------------------------------------------------

class PipelinePlan(BaseModel):
    """Structured plan output by the Planner agent."""
    intent: str = Field(description="chat | query | control")
    rationale: str = Field(default="", description="Why this plan")
    steps: list[dict[str, Any]] = Field(default_factory=list, description="Action steps")


# ---------------------------------------------------------------------------
# LLM factory (unchanged)
# ---------------------------------------------------------------------------

def _build_llm(
    model_name: str | None = None,
    temperature: float = 0.7,
    reasoning_effort: str = "high",
    enable_thinking: bool = True,
) -> ChatOpenAI:
    kwargs: dict[str, Any] = {
        "model": model_name or os.getenv("LLM_MODEL", "deepseek-chat"),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
        "temperature": temperature,
    }
    if enable_thinking:
        kwargs["reasoning_effort"] = reasoning_effort
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    else:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return ChatOpenAI(**kwargs)


# ---------------------------------------------------------------------------
# Dynamic prompt builders — one per agent role
# ---------------------------------------------------------------------------

def _extract_context(request: Any) -> AgentContext:
    """Pull AgentContext from a middleware request."""
    ctx: AgentContext | None = None
    runtime = getattr(request, "runtime", None)
    if runtime is not None:
        ctx = getattr(runtime, "context", None) or getattr(runtime, "args", None)
    if ctx is None:
        ctx = getattr(request, "context", None)
    return ctx or AgentContext()


def _format_tools(tools: list[Any]) -> str:
    lines = []
    for t in tools:
        name = getattr(t, "name", str(t))
        desc = getattr(t, "description", "")
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines) if lines else "No tools available."


def _make_prompt_builder(template_name: str):
    """Create a dynamic prompt function for a specific template."""
    def builder(request: Any) -> str:
        ctx = _extract_context(request)
        tools = list(getattr(request, "tools", []))
        tools_desc = _format_tools(tools)

        extra = ""
        if ctx.user_role == "viewer":
            extra = "You have read-only access."
        elif ctx.user_role == "operator":
            extra = "You can control devices."

        # The executor and observer receive plan/result through context
        plan_json = getattr(ctx, "plan_json", "{}") or "{}"
        exec_results = getattr(ctx, "execution_results", "") or ""
        perception_ctx = getattr(ctx, "perception_context", "") or ""

        prompt = prompts.get_template(template_name).partial(
            role=ctx.user_role,
            session_id=ctx.session_id,
            tools_description=tools_desc,
            plan_json=plan_json,
            execution_results=exec_results,
            extra_instructions=extra,
            perception_context=perception_ctx,
        )
        # Guarantee str — BaseMessage.content can be str | list
        content = prompt.format_messages()[0].content
        return content if isinstance(content, str) else str(content)

    return builder


# ---------------------------------------------------------------------------
# Event-emitting callback (shared)
# ---------------------------------------------------------------------------

def _sanitize(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable objects to their string repr.

    LangChain types (``ToolMessage``, ``AIMessage``, etc.) and any other
    objects that ``json.dumps`` can't handle are converted to strings so they
    can safely flow through EventBus → WebSocket / SSE.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    # Fallback: anything else → string
    try:
        s = str(obj)
        return s if s else repr(obj)
    except Exception:
        return repr(obj)


async def _on_agent_event(
    event_name: str,
    data: dict[str, Any],
    *,
    session_id: str = "",
    tool_name: str = "",
) -> None:
    """Emit a CloudEvent for a LangChain agent lifecycle event.

    ``data`` is sanitized before emission so raw LangChain objects
    (``ToolMessage``, ``AIMessage``) never reach the serialization layer.
    """
    bus = get_bus()
    event_type_map = {
        "on_chat_model_start": "wot.agent.thought",
        "on_tool_start": "wot.agent.action.started",
        "on_tool_end": "wot.agent.action.completed",
        "on_tool_error": "wot.agent.action.failed",
        "on_chain_end": "wot.agent.response",
    }
    wot_type = event_type_map.get(event_name, "wot.system.log")
    enriched = _sanitize(data)
    if tool_name:
        enriched["_tool_name"] = tool_name
    await bus.emit(Event(
        source=f"wotagent/agent/{event_name}",
        type=wot_type,
        data=enriched,
        session_id=session_id,
    ))


# ---------------------------------------------------------------------------
# Pipeline container
# ---------------------------------------------------------------------------

@dataclass
class AgentPipeline:
    """Container for the four agents that make up the WoT pipeline."""
    planner: Any
    executor: Any
    observer: Any
    responder: Any


def create_wot_pipeline(
    *,
    model_name: str | None = None,
    temperature: float = 0.7,
    reasoning_effort: str = "high",
    enable_thinking: bool = True,
    max_tool_calls_per_plan: int = 20,
) -> AgentPipeline:
    """Create the multi-agent pipeline.

    Returns an ``AgentPipeline`` with ``.planner``, ``.executor``,
    ``.observer``, and ``.responder`` attributes.
    """
    llm = _build_llm(
        model_name=model_name,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        enable_thinking=enable_thinking,
    )
    tools = get_all_tools(include_mcp=False)

    # ── Planner ──
    planner_mw = [
        dynamic_prompt(_make_prompt_builder("planner")),
        ModelRetryMiddleware(max_retries=1, retry_on=(Exception,)),
    ]
    planner = create_agent(
        model=llm,
        tools=tools,  # planner sees all tools in prompt but is told not to call them
        system_prompt="",  # handled by dynamic_prompt
        middleware=planner_mw,
        context_schema=AgentContext,
        name="planner",
    )

    # ── Executor ──
    executor_mw = [
        dynamic_prompt(_make_prompt_builder("executor")),
        ToolCallLimitMiddleware(thread_limit=max_tool_calls_per_plan),
        ModelRetryMiddleware(max_retries=2, retry_on=(Exception,)),
    ]
    executor = create_agent(
        model=llm,
        tools=tools,
        system_prompt="",
        middleware=executor_mw,
        context_schema=AgentContext,
        name="executor",
    )

    # ── Observer ──
    observer_mw = [
        dynamic_prompt(_make_prompt_builder("observer")),
    ]
    observer = create_agent(
        model=llm,
        tools=[],  # observer has no tools — only validates
        system_prompt="",
        middleware=observer_mw,
        context_schema=AgentContext,
        name="observer",
    )

    # ── Responder (chat shortcut) ──
    responder_mw = [
        dynamic_prompt(_make_prompt_builder("responder")),
    ]
    responder = create_agent(
        model=llm,
        tools=[],
        system_prompt="",
        middleware=responder_mw,
        context_schema=AgentContext,
        name="responder",
    )

    logger.info(
        "Pipeline created | model=%s tools=%d",
        llm.model, len(tools),
    )
    return AgentPipeline(
        planner=planner,
        executor=executor,
        observer=observer,
        responder=responder,
    )


# ---------------------------------------------------------------------------
# Backward-compatible single-agent factory
# ---------------------------------------------------------------------------

def create_wot_agent(
    *,
    model_name: str | None = None,
    temperature: float = 0.7,
    reasoning_effort: str = "high",
    enable_thinking: bool = True,
    max_tool_calls_per_plan: int = 20,
    system_prompt: str | None = None,
    name: str = "wot_agent",
) -> Any:
    """Legacy single ReAct agent. Prefer ``create_wot_pipeline``."""
    llm = _build_llm(
        model_name=model_name,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        enable_thinking=enable_thinking,
    )
    tools = get_all_tools(include_mcp=False)
    middleware = [
        dynamic_prompt(_make_prompt_builder("default")),
        ToolCallLimitMiddleware(thread_limit=max_tool_calls_per_plan),
        ModelRetryMiddleware(max_retries=2, retry_on=(Exception,)),
    ]
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware,
        context_schema=AgentContext,
        name=name,
    )
    logger.info("Legacy agent created | model=%s tools=%d", llm.model, len(tools))
    return agent


# ---------------------------------------------------------------------------
# Pipeline invocation
# ---------------------------------------------------------------------------

async def execute_plan_directly(
    pipeline: AgentPipeline,
    plan_json: str,
    plan: dict[str, Any],
    session_id: str = "",
    user_role: str = "operator",
    *,
    memory: ConversationMemory | None = None,
) -> str:
    """Execute a pre-structured plan (e.g. from perception rules) through
    Executor → Observer, **skipping the Planner** entirely.

    This is the entry point for rule-triggered actions. Returns the final
    response text.
    """
    bus = get_bus()

    context = AgentContext(
        session_id=session_id,
        user_role=user_role,
        user_message=f"[auto] rule triggered: {plan.get('rationale', '')}",
        plan_json=plan_json,
        execution_results="",
    )

    await bus.emit(Event(
        source="wotagent/perception",
        type="wot.session.started",
        subject=f"session/{session_id}",
        data={"trigger": plan.get("rationale", ""), "plan": plan},
        session_id=session_id,
    ))

    final_text = ""
    exec_final = ""

    try:
        # Phase 1: Executor — run the plan (skip Planner)
        message = f"Execute this plan: {plan_json}"
        exec_context = AgentContext(
            session_id=session_id,
            user_role=user_role,
            user_message=message,
            plan_json=plan_json,
        )
        async for event in pipeline.executor.astream_events(
            {"messages": [HumanMessage(content=message)]},
            context=exec_context,
            version="v2",
        ):
            kind = event.get("event", "")
            data = event.get("data", {})

            if kind in ("on_tool_start", "on_tool_end", "on_tool_error"):
                await _on_agent_event(kind, data, session_id=session_id,
                                      tool_name=event.get("name", ""))

            if kind == "on_chain_end":
                output = data.get("output", {})
                if isinstance(output, dict):
                    msg_list = output.get("messages", [])
                    if msg_list and hasattr(msg_list[-1], "content"):
                        exec_final = msg_list[-1].content or ""

        # Phase 2: Observer — respond to the user naturally
        obs_context = AgentContext(
            session_id=session_id,
            user_role=user_role,
            user_message=message,
            plan_json=plan_json,
            execution_results=exec_final or "(no output)",
        )
        obs_result = await pipeline.observer.ainvoke(
            {"messages": [HumanMessage(content=f"回复用户。")]},
            context=obs_context,
        )
        final_text = obs_result["messages"][-1].content or exec_final

    except Exception as exc:
        logger.exception("Direct plan execution failed")
        final_text = f"Auto-execution error: {exc}"

    # Memory
    if memory is not None:
        memory.add_ai_message(final_text)
        memory.save()

    await bus.emit(Event(
        source="wotagent/perception",
        type="wot.agent.response",
        data={"response": final_text, "triggered_by": plan.get("triggered_by", "")},
        session_id=session_id,
    ))
    await bus.emit(Event(
        source="wotagent/perception",
        type="wot.session.ended",
        subject=f"session/{session_id}",
        session_id=session_id,
    ))

    return final_text


def _serialize_messages(msgs: list[Any]) -> list[dict[str, Any]]:
    """Convert LangChain message objects to plain dicts for logging."""
    result = []
    for m in msgs:
        role = ""
        if hasattr(m, "type"):
            role = m.type
        elif isinstance(m, dict):
            role = m.get("role", "unknown")
        content = m.content if hasattr(m, "content") else (m.get("content", "") if isinstance(m, dict) else str(m))
        entry: dict[str, Any] = {"role": role, "content": content}
        # Tool calls on assistant messages
        if hasattr(m, "tool_calls") and m.tool_calls:
            calls = []
            for tc in m.tool_calls:
                calls.append({
                    "id": tc.get("id", ""),
                    "name": tc.get("name", ""),
                    "args": tc.get("args", {}),
                })
            if calls:
                entry["tool_calls"] = calls
        result.append(entry)
    return result


async def invoke_agent_stream(
    pipeline: AgentPipeline,
    message: str,
    session_id: str = "",
    user_role: str = "operator",
    *,
    memory: ConversationMemory | None = None,
    state: ConversationState | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Async generator that runs the full pipeline and yields structured chunks.

    Yields dicts with ``type``:

    - ``"plan"`` — structured plan from planner
    - ``"thinking"`` — DeepSeek ``reasoning_content``
    - ``"token"`` — a response text fragment
    - ``"wot.agent.action.started/completed/failed"`` — tool events
    - ``"error"`` — invocation error
    - ``"done"`` — final chunk with ``response`` and ``session_id``
    """
    bus = get_bus()

    # ── Transcript recorder ──
    transcript = SessionTranscript(session_id) if session_id else None
    if transcript is not None:
        transcript.record_start(model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                                 role=user_role)

    # Inject perception context if available
    perception_ctx = ""
    try:
        from ..perception import get_perception_engine
        engine = get_perception_engine(auto_start=False)
        if engine.is_running:
            perception_ctx = engine.get_context()
    except Exception:
        pass

    context = AgentContext(
        session_id=session_id,
        user_role=user_role,
        user_message=message,
        plan_json="{}",
        execution_results="",
        perception_context=perception_ctx,
    )

    # Build message list with memory
    input_messages = [HumanMessage(content=message)]
    if memory is not None:
        input_messages = list(memory.messages) + input_messages

    if transcript is not None:
        transcript.record_user_message(message)

    await bus.emit(Event(
        source="wotagent/core/pipeline",
        type="wot.session.started",
        subject=f"session/{session_id}",
        data={"message": message, "role": user_role},
        session_id=session_id,
    ))

    final_text = ""
    plan_raw = "{}"
    last_token_text = ""
    last_thinking_text = ""
    streamed_token_output = False

    try:
        # ═══════════════════════════════════════════════
        # Phase 1: Intent classification via tool-calling
        # ═══════════════════════════════════════════════

        # Fast LLM with tool-calling for structured intent classification
        intent_model = _build_llm(
            temperature=0,
            enable_thinking=False,
        )
        intent_schema = {
            "type": "function",
            "function": {
                "name": "classify_intent",
                "description": "对用户消息进行意图分类",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": ["chat", "query", "control"],
                            "description": "chat=纯聊天问候/感谢, query=询问设备或环境状态, control=控制/操作设备或环境抱怨",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "分类理由（中文，简练）",
                        },
                    },
                    "required": ["intent", "rationale"],
                },
            },
        }
        intent_model_with_tool = intent_model.bind(
            tools=[intent_schema],
            tool_choice={"type": "function", "function": {"name": "classify_intent"}},
        )

        intent_prompt = (
            "你对用户消息进行意图分类，只输出分类结果，不要额外回复。\n\n"
            "分类规则：\n"
            "- **control**：用户想操作设备（打开/关闭/调节/设置），或抱怨环境（太热/太暗/太吵/有点冷等隐含调节需求）\n"
            "- **query**：用户询问设备或环境状态（多少度/什么状态/显示设备/怎么样/是否开着）\n"
            "- **chat**：纯闲聊（你好/谢谢/再见/今天天气不错）—— 不涉及任何设备或环境\n\n"
            "注意：任何涉及设备名词（灯、空调、风扇、温度、湿度等）的消息都不是chat。"
        )

        # ── 方案A: Inject conversation context ──
        if state is not None:
            ctx = state.get_context_str()
            if ctx:
                intent_prompt += (
                    "\n\n## 最近对话上下文\n" + ctx + "\n"
                    "请结合上面对话来理解用户最新消息的意图。"
                )

        # ── 方案B: Continuation hint ──
        if state is not None:
            hint = state.get_continuation_hint()
            if hint:
                intent_prompt += "\n\n## 意图延续提示\n" + hint

        intent_result = await intent_model_with_tool.ainvoke([
            ("system", intent_prompt),
            ("human", message),
        ])

        # Parse tool call — prefer AIMessage.tool_calls attr, fall back to additional_kwargs
        intent_data = {"intent": "chat", "rationale": "分类模型未返回工具调用"}
        tool_calls = getattr(intent_result, "tool_calls", None)
        if not tool_calls:
            raw = intent_result.additional_kwargs.get("tool_calls", [])
            if raw:
                try:
                    intent_data = json.loads(raw[0]["function"]["arguments"])
                except (KeyError, json.JSONDecodeError):
                    pass
        else:
            tc = tool_calls[0]
            if tc.get("name") == "classify_intent":
                intent_data = tc.get("args", intent_data)

        intent = intent_data.get("intent", "chat")
        rationale = intent_data.get("rationale", "")

        # Update conversation state with classified intent
        if state is not None:
            state.intent = intent if intent != "chat" else state.intent

        plan = {
            "intent": intent,
            "rationale": rationale,
            "steps": [],
        }
        plan_raw = json.dumps(plan, ensure_ascii=False)

        yield {"type": "plan", "content": plan}
        await bus.emit(Event(
            source="wotagent/core/pipeline",
            type="wot.agent.plan",
            data={"plan": plan},
            session_id=session_id,
        ))
        if transcript is not None:
            transcript.record_plan(intent=plan["intent"],
                                    rationale=plan.get("rationale", ""),
                                    steps=plan.get("steps", []))

        intent = plan.get("intent", "chat")

        # ═══════════════════════════════════════════════
        # Phase 2: Route
        # ═══════════════════════════════════════════════

        if intent == "chat":
            # ── Responder (direct chat, no tools) ──
            async for event in pipeline.responder.astream_events(
                {"messages": input_messages},
                context=AgentContext(
                    session_id=session_id,
                    user_role=user_role,
                    user_message=message,
                    plan_json=plan_raw,
                ),
                version="v2",
            ):
                kind = event.get("event", "")
                data = event.get("data", {})

                # Capture LLM input for transcript
                if transcript is not None and kind == "on_chat_model_start":
                    try:
                        msgs = data.get("input", {}).get("messages", [])
                        if msgs:
                            transcript.record_llm_input("responder", _serialize_messages(msgs))
                    except Exception:
                        pass

                if kind == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    if chunk is None:
                        continue

                    rc = chunk.additional_kwargs.get("reasoning_content", "")
                    if rc:
                        if rc.startswith(last_thinking_text):
                            rc_delta = rc[len(last_thinking_text):]
                        else:
                            rc_delta = rc
                        last_thinking_text = rc
                        if rc_delta:
                            await bus.emit(Event(
                                source="wotagent/core/pipeline",
                                type="wot.agent.thought",
                                data={"content": rc_delta},
                                session_id=session_id,
                            ))
                            yield {"type": "thinking", "content": rc_delta}

                    content = chunk.content if isinstance(chunk.content, str) else ""
                    if content:
                        if content.startswith(last_token_text):
                            token_delta = content[len(last_token_text):]
                        else:
                            token_delta = content
                        last_token_text = content
                        if token_delta:
                            streamed_token_output = True
                            await bus.emit(Event(
                                source="wotagent/core/pipeline",
                                type="wot.agent.token",
                                data={"content": token_delta},
                                session_id=session_id,
                            ))
                            yield {"type": "token", "content": token_delta}

                if kind == "on_chain_end":
                    output = data.get("output", {})
                    if isinstance(output, dict):
                        msg_list = output.get("messages", [])
                        if msg_list and hasattr(msg_list[-1], "content"):
                            final_text = msg_list[-1].content or final_text
                    elif hasattr(output, "content"):
                        final_text = output.content or final_text

        else:
            # ── Executor (tool execution) ──
            exec_context = AgentContext(
                session_id=session_id,
                user_role=user_role,
                user_message=message,
                plan_json=plan_raw,
            )
            exec_final = ""
            async for event in pipeline.executor.astream_events(
                {"messages": input_messages},
                context=exec_context,
                version="v2",
            ):
                kind = event.get("event", "")
                data = event.get("data", {})

                # Tool events
                if kind in ("on_tool_start", "on_tool_end", "on_tool_error"):
                    await _on_agent_event(kind, data, session_id=session_id,
                                          tool_name=event.get("name", ""))
                    wot_type = {
                        "on_tool_start": "wot.agent.action.started",
                        "on_tool_end": "wot.agent.action.completed",
                        "on_tool_error": "wot.agent.action.failed",
                    }[kind]
                    yield {"type": wot_type, "data": dict(data)}

                    # Record tool calls for transcript
                    if transcript is not None and kind == "on_tool_end":
                        tool_name = event.get("name", "")
                        inp = data.get("input", "")
                        out = data.get("output", "")
                        transcript.record_tool_call(tool=tool_name, input=inp, output=out)

                # Capture LLM input for transcript
                if transcript is not None and kind == "on_chat_model_start":
                    try:
                        msgs = data.get("input", {}).get("messages", [])
                        if msgs:
                            transcript.record_llm_input("executor", _serialize_messages(msgs))
                    except Exception:
                        pass

                # Capture executor's final output for observer
                if kind == "on_chain_end":
                    output = data.get("output", {})
                    if isinstance(output, dict):
                        msg_list = output.get("messages", [])
                        if msg_list and hasattr(msg_list[-1], "content"):
                            exec_final = msg_list[-1].content or ""

            # ═══════════════════════════════════════════════
            # Phase 3: Observer — respond to the user naturally
            # ═══════════════════════════════════════════════
            obs_context = AgentContext(
                session_id=session_id,
                user_role=user_role,
                user_message=message,
                plan_json=plan_raw,
                execution_results=exec_final or "(no output from executor)",
            )
            obs_result = await pipeline.observer.ainvoke(
                {"messages": [HumanMessage(content=f"用户说: {message}\n回复用户。")]},
                context=obs_context,
            )
            final_text = obs_result["messages"][-1].content or exec_final
            if final_text and not streamed_token_output:
                await bus.emit(Event(
                    source="wotagent/core/pipeline",
                    type="wot.agent.token",
                    data={"content": final_text},
                    session_id=session_id,
                ))
                yield {"type": "token", "content": final_text}

    except Exception as exc:
        logger.exception("Pipeline invocation failed")
        await bus.emit(Event(
            source="wotagent/core/pipeline",
            type="wot.agent.error",
            data={"error": str(exc)},
            session_id=session_id,
        ))
        yield {"type": "error", "content": str(exc)}
        final_text = f"I encountered an error: {exc}"

    # ── Save memory ──
    if memory is not None:
        memory.add_user_message(message)
        memory.add_ai_message(final_text)
        memory.save()

    # ── Save transcript ──
    if transcript is not None:
        transcript.record_assistant_response(final_text)
        transcript.record_end()
        transcript.save()

    # ── Record conversation state ──
    if state is not None:
        state.record_turn(message, final_text)

    await bus.emit(Event(
        source="wotagent/core/pipeline",
        type="wot.agent.response",
        data={"response": final_text},
        session_id=session_id,
    ))
    await bus.emit(Event(
        source="wotagent/core/pipeline",
        type="wot.session.ended",
        subject=f"session/{session_id}",
        session_id=session_id,
    ))

    yield {"type": "done", "response": final_text, "session_id": session_id}


async def invoke_agent(
    pipeline: AgentPipeline,
    message: str,
    session_id: str = "",
    user_role: str = "operator",
    *,
    memory: ConversationMemory | None = None,
    state: ConversationState | None = None,
) -> dict[str, Any]:
    """Non-streaming convenience wrapper around ``invoke_agent_stream``."""
    final = {"response": "", "session_id": session_id}
    async for chunk in invoke_agent_stream(
        pipeline, message, session_id, user_role, memory=memory, state=state,
    ):
        if chunk["type"] == "done":
            final = {"response": chunk["response"], "session_id": chunk["session_id"]}
        elif chunk["type"] == "error":
            final = {"response": chunk["content"], "session_id": session_id}
    return final
