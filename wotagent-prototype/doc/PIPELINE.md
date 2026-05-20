# Multi-Agent Pipeline

## Why Pipeline Instead of Single Agent

A single ReAct agent (the default `create_agent` pattern) works by giving the LLM
tools and letting it loop: *think → call tool → observe → think again → ...*

Problems for IoT:

1. **No explicit plan** — The LLM's reasoning is opaque. You can't inspect "what it
   plans to do" before it does it.
2. **No chat shortcut** — Even simple greetings trigger the full tool-calling loop.
3. **No validation** — After calling tools, the agent may or may not check results.
4. **Hard to audit** — No structured record of "what was planned vs what happened."
5. **No environment awareness** — No background monitoring; the LLM must query each time.

The pipeline solves these by splitting responsibility:

- **Intent Classifier** (new): Tool-calling LLM (temperature=0) for structured intent classification
- **Perception Engine**: "What's happening?" → rule-based environment monitoring
- **Intent Classifier**: "What's the intent?" → tool-calling LLM (temperature=0)
- **Executor**: "Do it." → tool calls
- **Observer**: "Did it work?" → format natural-language response

## AgentPipeline

```python
@dataclass
class AgentPipeline:
    executor: Any    # create_agent() with EXECUTOR_PROMPT + all tools
    observer: Any    # create_agent() with OBSERVER_PROMPT + no tools
    responder: Any   # create_agent() with RESPONDER_PROMPT + no tools
    planner: Any     # (reserved — currently replaced by Intent Classifier)
```

Created by `create_wot_pipeline()`:

```python
from wotagent.core import create_wot_pipeline

pipeline = create_wot_pipeline(enable_thinking=True)
# pipeline.executor, pipeline.observer, pipeline.responder (primary)
# pipeline.planner (reserved)
```

### Intent Classifier (Step 0)

Intent is classified **before** the pipeline runs, using a separate lightweight
tool-calling LLM call (temperature=0, thinking disabled):

```python
intent_model = _build_llm(temperature=0, enable_thinking=False)
intent_model_with_tool = intent_model.bind(
    tools=[intent_schema],  # classify_intent function with enum: chat|query|control
    tool_choice={"type": "function", "function": {"name": "classify_intent"}},
)
intent_result = await intent_model_with_tool.ainvoke([
    ("system", intent_prompt + context_injection + continuation_hint),
    ("human", message),
])
# Parses tool_call args → {"intent": "control", "rationale": "..."}
```

This is more accurate than a keyword-based classifier since it uses the same LLM
as the rest of the pipeline but with zero extra model initialization cost
(temperature=0, no thinking).

Key design points:
- **Fast path**: temperature=0 produces deterministic, single-token-choice output
- **Structured output**: Tool-calling API returns JSON, no parsing needed
- **Cheap**: No `reasoning_content`, no tool-calling loop — one model invocation

## Rule-Triggered Execution

For rules triggered by the Perception Engine, the Planner is **skipped entirely**:

```python
from wotagent.core import execute_plan_directly

final_text = await execute_plan_directly(
    pipeline, plan_json, plan,
    session_id="sess_xxx",
    user_role="operator",
    memory=memory,
)
# Runs Executor → Observer only (no LLM for intent parsing)
```

## Agent Prompt Templates

Each agent role has its own system prompt template:

| Agent | Template | Key Variables |
|-------|----------|--------------|
| Executor | `EXECUTOR_PROMPT_V1` | `{role}`, `{tools_description}`, `{plan_json}` |
| Observer | `OBSERVER_PROMPT_V1` | `{plan_json}`, `{execution_results}` |
| Responder | `RESPONDER_PROMPT_V1` | `{role}`, `{extra_instructions}` |
| Planner (reserved) | `PLANNER_PROMPT_V1` | `{role}`, `{perception_context}`, `{tools_description}` |

The prompts are registered in `prompts/templates.py` and loaded via the
`dynamic_prompt` middleware with a builder per role (`_make_prompt_builder`).

## Prompt Builder

```python
_make_prompt_builder("executor")  → reads role/session/plan_json from AgentContext
_make_prompt_builder("observer")  → reads plan_json + execution_results
_make_prompt_builder("responder") → basic chat prompt
_make_prompt_builder("planner")   → reserved (Intent Classifier replaces Planner)
```

Each builder extracts `AgentContext` from the middleware `request` object at every
agent step, ensuring the prompt is always up-to-date. The `perception_context`
field is populated from `PerceptionEngine.get_context()` at invocation time —
the LLM sees current device states without querying.

## Streaming

`invoke_agent_stream()` yields an async generator with typed chunks:

```
Phase 0 (Intent Classifier — transparent to consumer):
  (internal tool-calling LLM call, no events yielded)
  └── yield {"type": "plan", "content": {"intent": "control", "steps": [...]}}

Phase 1a (Responder, if chat intent):
  yield {"type": "thinking", "content": "..."}   # DeepSeek reasoning
  yield {"type": "token", "content": "你好！"}      # response tokens

Phase 1b (Executor, if control/query intent):
  yield {"type": "wot.agent.action.started", "data": {...}}
  yield {"type": "wot.agent.action.completed", "data": {...}}

Phase 2 (Observer, if control/query intent):
  yield {"type": "token", "content": "客厅灯已调暗到30%"}

Final:
  yield {"type": "done", "response": "...", "session_id": "..."}
```

### Perception-triggered stream (via `execute_plan_directly`)

```
EventBus: wot.perception.rule_triggered
  │
  └── execute_plan_directly()
       │
       ├── emit wot.session.started
       │
       ├── executor.astream_events()
       │   ├── action.started
       │   ├── action.completed
       │   └── action.failed
       │
       ├── observer.ainvoke()
       │
       ├── emit wot.agent.response
       ├── emit wot.session.ended
       └── return final_text
```

No `{"type": "plan"}` chunk — the plan was already pre-structured by the rule engine.

## Memory Integration

Memory is handled transparently in `invoke_agent_stream()`:

1. **Before invocation**: prepend `memory.messages` to the input
2. **After invocation**: call `memory.add_user_message()`, `memory.add_ai_message()`,
   and `memory.save()`

The intent classifier receives only the *last user message* plus context injection
(方案A + 方案B). The executor/responder receive the full message history for context.

## Session Transcript

When `session_id` is provided to `invoke_agent_stream()`, a `SessionTranscript` is
automatically created and events are recorded throughout the pipeline:

```
invoke_agent_stream(message, session_id="sess_xxx")
  │
  ├── SessionTranscript(session_id) created
  ├── transcript.record_start(model=..., role=...)
  │
  ├── Phase 0 (Intent Classifier)
  │   ├── transcript.record_user_message(message)
  │   └── transcript.record_plan(intent, rationale, steps)
  │
  ├── Phase 1/2 (Executor/Responder)
  │   ├── transcript.record_llm_input(agent, messages)   ← on_chat_model_start
  │   └── transcript.record_tool_call(tool, input, output) ← on_tool_end
  │
  └── After pipeline completes
      ├── transcript.record_assistant_response(final_text)
      ├── transcript.record_end()
      └── transcript.save()  → {session_id}.full.jsonl + .chat.jsonl
```

Two JSONL files per session:
- `data/memory/{session_id}.full.jsonl` — All events with timestamps
- `data/memory/{session_id}.chat.jsonl` — User/assistant turns only

Non-serializable objects (LangChain `ToolMessage`, `AIMessage`, etc.) are
automatically converted to strings via `_sanitize()`.

## Intent Continuity

The `state` parameter (`ConversationState`) provides cross-turn intent tracking:

```python
state = ConversationState()
async for chunk in invoke_agent_stream(..., state=state):
    ...
# state.intent → "control" (preserved from previous turn)
```

Two injection mechanisms inside the intent classifier:

**方案A — Context injection** (`get_context_str()`):
Injects up to 2 recent user/assistant message pairs into the classifier prompt,
so follow-up messages like "我在客厅呢" are understood in context.

**方案B — Continuation hint** (`get_continuation_hint()`):
If the previous intent was non-chat and the agent asked a question, injects:
```
⚠️ 提示：上一步的操作意图是「control」，助手刚问了用户一个问题，
现在用户在回答。如果用户在回答助手的问题，意图应延续上一步的
「control」，而不是chat。
```

**State lifecycle:**
- After classification: `state.intent = intent if intent != "chat" else state.intent`
- After turn completion: `state.record_turn(user_msg, agent_msg)` which tracks:
  - Whether the agent asked a question (for next turn's 方案B)
  - Last 5 exchanges (for 方案A context)
  - Resets `intent` to `None` when agent returns to idle chat

## From Pipeline to StateGraph

The current architecture uses Python-level orchestration (three sequential
`astream_events` calls). This maps cleanly to a LangGraph `StateGraph`:

```python
from langgraph.graph import StateGraph
from typing import TypedDict, Annotated

class PipelineState(TypedDict):
    messages: list
    context: AgentContext
    plan: str
    execution_results: str
    final_response: str

builder = StateGraph(PipelineState)

# Nodes — each wraps a create_agent() call
builder.add_node("planner", planner_node)
builder.add_node("executor", executor_node)
builder.add_node("observer", observer_node)
builder.add_node("responder", responder_node)

# Conditional routing
builder.add_conditional_edges(
    "planner",
    lambda s: "respond" if json.loads(s["plan"]).get("intent") == "chat" else "execute",
)
builder.add_edge("executor", "observer")
```

Switching to StateGraph would add:
- **Built-in state management** instead of manual context passing
- **Checkpointing** for pause/resume long-running plans
- **Parallel execution** for independent plan steps
- **Visual tracing** via LangGraph Studio

The current implementation is designed so this migration is mechanical — no
logic changes, just structural.
