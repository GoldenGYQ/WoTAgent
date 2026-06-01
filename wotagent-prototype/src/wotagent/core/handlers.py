"""Pipeline invocation handlers -- stream, direct-exec, and convenience wrappers."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage

from ..events import Event, get_bus
from ..memory import ConversationMemory, SessionTranscript
from .models import AgentContext
from .pipeline import AgentPipeline
from .pipeline import _build_llm
from .state import ConversationState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event-emitting helpers
# ---------------------------------------------------------------------------


def _sanitize(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable objects to string repr."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
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
    """Emit a CloudEvent for a LangChain agent lifecycle event."""
    bus = get_bus()
    event_type_map: dict[str, str] = {
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
# Direct plan execution (perception auto-trigger path)
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
    """Execute a pre-structured plan through Executor -> Observer."""
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

        obs_context = AgentContext(
            session_id=session_id,
            user_role=user_role,
            user_message=message,
            plan_json=plan_json,
            execution_results=exec_final or "(no output)",
        )
        obs_result = await pipeline.observer.ainvoke(
            {"messages": [HumanMessage(content="Reply to the user.")]},
            context=obs_context,
        )
        final_text = obs_result["messages"][-1].content or exec_final

    except Exception as exc:
        logger.exception("Direct plan execution failed")
        final_text = f"Auto-execution error: {exc}"

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


# ---------------------------------------------------------------------------
# Message serialization
# ---------------------------------------------------------------------------


def _serialize_messages(msgs: list[Any]) -> list[dict[str, Any]]:
    """Convert LangChain message objects to plain dicts for logging."""
    result: list[dict[str, Any]] = []
    for m in msgs:
        role = ""
        if hasattr(m, "type"):
            role = m.type
        elif isinstance(m, dict):
            role = m.get("role", "unknown")
        content = m.content if hasattr(m, "content") else (m.get("content", "") if isinstance(m, dict) else str(m))
        entry: dict[str, Any] = {"role": role, "content": content}
        if hasattr(m, "tool_calls") and m.tool_calls:
            calls: list[dict[str, Any]] = []
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


# ---------------------------------------------------------------------------
# Streaming pipeline invocation (main entry point)
# ---------------------------------------------------------------------------


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
    - ``"plan"`` -- structured plan from intent classification
    - ``"thinking"`` -- DeepSeek ``reasoning_content``
    - ``"token"`` -- a response text fragment
    - ``"wot.agent.action.started/completed/failed"`` -- tool events
    - ``"error"`` -- invocation error
    - ``"done"`` -- final chunk with ``response`` and ``session_id``
    """
    bus = get_bus()

    transcript = SessionTranscript(session_id) if session_id else None
    if transcript is not None:
        transcript.record_start(model=os.getenv("LLM_MODEL", "deepseek-v4-flash"),
                                 role=user_role)

    perception_ctx = ""
    try:
        from ..perception import get_perception_engine
        engine = get_perception_engine(auto_start=False)
        if engine.is_running:
            perception_ctx = await engine.get_context()
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
    streamed_token_output = False
    streamed_thinking = False
    thinking_buffer = ""
    thinking_batch_emitted = False

    try:
        # ---- Phase 1: Intent classification via tool-calling ----
        intent_model = _build_llm(temperature=0, enable_thinking=False)
        intent_schema = {
            "type": "function",
            "function": {
                "name": "classify_intent",
                "description": "Classify user message intent",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": ["chat", "query", "control"],
                            "description": "chat=pure chat, query=ask about device/environment, control=operate device/environment",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "Classification rationale (concise)",
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
            "Classify the user message intent. Output only the classification.\n\n"
            "Rules:\n"
            "- **control**: user wants to operate devices or complains about environment\n"
            "- **query**: user asks about device or environment state\n"
            "- **chat**: pure chat/greeting -- does not involve any device or environment\n\n"
            "Note: any message containing device names (light, AC, fan, temperature, humidity) is NOT chat."
        )

        if state is not None:
            ctx = state.get_context_str()
            if ctx:
                intent_prompt += "\n\n## Recent conversation context\n" + ctx
            hint = state.get_continuation_hint()
            if hint:
                intent_prompt += "\n\n## Intent continuation hint\n" + hint

        intent_result = await intent_model_with_tool.ainvoke([
            ("system", intent_prompt),
            ("human", message),
        ])

        intent_data: dict[str, Any] = {"intent": "chat", "rationale": "No tool call returned"}
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
                intent_data = tc.get("args", tc.get("function", {}).get("arguments", {}))

        intent = intent_data.get("intent", "chat")
        rationale = intent_data.get("rationale", "")
        plan_raw = json.dumps({"intent": intent, "rationale": rationale}, ensure_ascii=False)

        yield {
            "type": "plan",
            "intent": intent,
            "rationale": rationale,
            "plan": plan_raw,
            "content": {"intent": intent, "rationale": rationale, "steps": []},
        }

        # ---- Route based on intent ----
        if intent == "chat":
            responder_context = AgentContext(
                session_id=session_id,
                user_role=user_role,
                user_message=message,
                plan_json=plan_raw,
                execution_results="",
            )
            async for event in pipeline.responder.astream_events(
                {"messages": input_messages},
                context=responder_context,
                version="v2",
            ):
                kind = event.get("event", "")
                data = event.get("data", {})

                if kind == "on_chat_model_start":
                    await _on_agent_event(kind, data, session_id=session_id)
                    yield {"type": "thinking", "content": ""}

                if kind in ("on_chain_stream", "on_chat_model_stream"):
                    delta = data.get("chunk", {})
                    if hasattr(delta, "content") and delta.content:
                        await bus.emit(Event(
                            source="wotagent/core/pipeline",
                            type="wot.agent.token",
                            data={"content": delta.content},
                            session_id=session_id,
                        ))
                        yield {"type": "token", "content": delta.content}
                        streamed_token_output = True
                    if hasattr(delta, "additional_kwargs"):
                        rc = delta.additional_kwargs.get("reasoning_content", "")
                        if rc:
                            await bus.emit(Event(
                                source="wotagent/core/pipeline",
                                type="wot.agent.thought",
                                data={"content": rc},
                                session_id=session_id,
                            ))
                            yield {"type": "thinking", "content": rc}
                            streamed_thinking = True
                            thinking_buffer += rc

                if kind == "on_chain_end":
                    output = data.get("output", {})
                    if isinstance(output, dict):
                        msg_list = output.get("messages", [])
                        if msg_list:
                            last_msg = msg_list[-1]
                            final_text = last_msg.content or ""
                            if not streamed_thinking and hasattr(last_msg, "additional_kwargs"):
                                rc = last_msg.additional_kwargs.get("reasoning_content", "")
                                if rc:
                                    await bus.emit(Event(
                                        source="wotagent/core/pipeline",
                                        type="wot.agent.thought",
                                        data={"content": rc},
                                        session_id=session_id,
                                    ))
                                    yield {"type": "thinking", "content": rc}
                                    streamed_thinking = True
                                    thinking_buffer += rc
            if final_text and not streamed_token_output:
                await bus.emit(Event(
                    source="wotagent/core/pipeline",
                    type="wot.agent.token",
                    data={"content": final_text},
                    session_id=session_id,
                ))
                yield {"type": "token", "content": final_text}
            if thinking_buffer and not thinking_batch_emitted:
                await bus.emit(Event(
                    source="wotagent/core/pipeline",
                    type="wot.agent.thought",
                    data={"content": thinking_buffer, "batch": True},
                    session_id=session_id,
                ))
                thinking_batch_emitted = True
        else:
            # Phase 2: Executor -- run tool calls
            exec_final = ""
            exec_context = AgentContext(
                session_id=session_id,
                user_role=user_role,
                user_message=message,
                plan_json=plan_raw,
            )
            async for event in pipeline.executor.astream_events(
                {"messages": input_messages},
                context=exec_context,
                version="v2",
            ):
                kind = event.get("event", "")
                data = event.get("data", {})

                if kind == "on_chat_model_start":
                    await _on_agent_event(kind, data, session_id=session_id)
                    yield {"type": "thinking", "content": ""}

                if kind in ("on_chain_stream", "on_chat_model_stream"):
                    delta = data.get("chunk", {})
                    if hasattr(delta, "additional_kwargs"):
                        rc = delta.additional_kwargs.get("reasoning_content", "")
                        if rc:
                            await bus.emit(Event(
                                source="wotagent/core/pipeline",
                                type="wot.agent.thought",
                                data={"content": rc},
                                session_id=session_id,
                            ))
                            yield {"type": "thinking", "content": rc}
                            thinking_buffer += rc

                if kind in ("on_tool_start", "on_tool_end", "on_tool_error"):
                    wot_type = {
                        "on_tool_start": "wot.agent.action.started",
                        "on_tool_end": "wot.agent.action.completed",
                        "on_tool_error": "wot.agent.action.failed",
                    }[kind]
                    yield {"type": wot_type, "data": dict(data)}

                    if transcript is not None and kind == "on_tool_end":
                        tool_name = event.get("name", "")
                        inp = data.get("input", "")
                        out = data.get("output", "")
                        transcript.record_tool_call(tool=tool_name, input=inp, output=out)

                if transcript is not None and kind == "on_chat_model_start":
                    try:
                        msgs = data.get("input", {}).get("messages", [])
                        if msgs:
                            transcript.record_llm_input("executor", _serialize_messages(msgs))
                    except Exception:
                        pass

                if kind == "on_chain_end":
                    output = data.get("output", {})
                    if isinstance(output, dict):
                        msg_list = output.get("messages", [])
                        if msg_list and hasattr(msg_list[-1], "content"):
                            last_msg = msg_list[-1]
                            exec_final = last_msg.content or ""
                            if not streamed_thinking and hasattr(last_msg, "additional_kwargs"):
                                rc = last_msg.additional_kwargs.get("reasoning_content", "")
                                if rc:
                                    await bus.emit(Event(
                                        source="wotagent/core/pipeline",
                                        type="wot.agent.thought",
                                        data={"content": rc},
                                        session_id=session_id,
                                    ))
                                    yield {"type": "thinking", "content": rc}
                                    streamed_thinking = True
                                    thinking_buffer += rc

            # Phase 3: Observer
            obs_context = AgentContext(
                session_id=session_id,
                user_role=user_role,
                user_message=message,
                plan_json=plan_raw,
                execution_results=exec_final or "(no output from executor)",
            )
            obs_result = await pipeline.observer.ainvoke(
                {"messages": [HumanMessage(content=f"User said: {message}\nReply to the user.")]},
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
            if thinking_buffer and not thinking_batch_emitted:
                await bus.emit(Event(
                    source="wotagent/core/pipeline",
                    type="wot.agent.thought",
                    data={"content": thinking_buffer, "batch": True},
                    session_id=session_id,
                ))
                thinking_batch_emitted = True

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

    if memory is not None:
        memory.add_user_message(message)
        memory.add_ai_message(final_text)
        memory.save()

    if transcript is not None:
        transcript.record_assistant_response(final_text)
        transcript.record_end()
        transcript.save()

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
    final: dict[str, Any] = {"response": "", "session_id": session_id}
    async for chunk in invoke_agent_stream(
        pipeline, message, session_id, user_role, memory=memory, state=state,
    ):
        if chunk["type"] == "done":
            final = {"response": chunk["response"], "session_id": chunk["session_id"]}
        elif chunk["type"] == "error":
            final = {"response": chunk["content"], "session_id": session_id}
    return final
