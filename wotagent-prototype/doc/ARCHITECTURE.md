# WoTAgent Architecture

## Overview

WoTAgent is an event-driven IoT smart-home agent built on LangChain 1.3+/LangGraph. It uses a **multi-agent pipeline** (Planner → Executor → Observer) to decouple intent recognition, device control, and result validation, plus a **rule-based Perception Engine** for automated environment monitoring.

```
                    ┌──────────────────┐
                    │  Perception      │  ←── Rule-based (0 LLM tokens)
                    │  Engine          │
                    │  (polling loop)  │
                    └───────┬──────────┘
                            │ triggered Plan
                            ▼
User ──→ ┌─────────────────────────────────────────────────────┐
          │                   AgentPipeline                     │
          │                                                     │
          │  ┌──────────────────┐    ┌──────────────────┐       │
          │  │ Intent Classifier│───▶│   Executor       │control│
          │  │ (tool-calling,   │    │ (tools + MCP)    │query  │
          │  │  temperature=0)  │    └────────┬─────────┘       │
          │  └────────┬─────────┘             │                 │
          │           │                       ▼                 │
          │           │                ┌────────────┐           │
          │           └─────chat─────▶│  Responder │           │
          │                           │ (no tools) │           │
          │                           └────────────┘           │
          │                                      │             │
          │                           ┌──────────┘             │
          │                           ▼                        │
          │                      ┌──────────┐                  │
          │                      │ Observer │  ← response      │
          │                      │(validate │    formatting    │
          │                      │ & reply) │                  │
          │                      └──────────┘                  │
          └─────────────────────────────────────────────────────┘
                  │              │              │
                  ▼              ▼              ▼
              Event Bus ───► WebSocket ──► Frontend (Vue)
              Event Bus ───► SSE (legacy) ──► Other consumers
              Event Bus ───► Log ──► wotagent.log
```
```

Two trigger paths:
- **User message** → Intent Classifier (tool-calling) → Executor/Observer — full pipeline
- **Rule triggered** → Executor/Observer directly — Planner/Classifier skipped

### Intent Continuity

The system tracks conversation context across turns via `ConversationState` to handle follow-up messages correctly. See [PIPELINE.md](PIPELINE.md#intent-continuity) for details.

## Modules

```
src/wotagent/
├── core/
│   ├── __init__.py    # Public API exports
│   ├── agent.py       # Multi-agent pipeline (Intent Classifier → Executor → Observer + Responder)
│   ├── session.py     # Session & memory lifecycle
│   └── state.py       # ConversationState — intent continuity across turns
├── events/
│   ├── schema.py      # CloudEvents 1.0 format
│   └── bus.py         # Pub/sub event bus with history replay
├── memory/
│   └── manager.py     # ConversationMemory (windowed + disk persistence)
│                       # SessionTranscript (dual JSONL transcript logging)
├── perception/
│   ├── rules.py       # Condition/EnvironmentRule models + defaults
│   └── engine.py      # PerceptionEngine (polling, rule evaluation, cooldown)
├── prompts/
│   └── templates.py   # 5 prompt templates for each agent role
├── tools/
│   ├── iot.py         # IoT device tools (list/control/query)
│   ├── registry.py    # System tools (psutil, file ops)
│   └── mcp_client.py  # MCP protocol client (FastMCP stdio/http)
├── wot/
│   └── td.py          # Thing Description loader & semantic discovery
├── auth/
│   └── rbac.py        # Role-based access control (admin/operator/viewer)
├── rag/
│   └── retriever.py   # ChromaDB vector retrieval for TD docs
├── api/
│   └── app.py         # FastAPI server with WebSocket + SSE endpoints
├── cli.py             # CLI entry point (repl, api, log subcommands)
└── logging/
    └── logger.py      # Structured logging with event bus integration
```

## Event Transport

All agent actions produce CloudEvents on the **EventBus**, which feeds two transports:

| Transport | Endpoint | Status |
|-----------|----------|--------|
| WebSocket | `ws://host/ws` | **Primary** — Vue frontend |
| SSE | `GET /api/events/{session_id}` and `/api/events/all` | **Legacy** — backward compatibility |

WebSocket also accepts command messages (`chat`, `ping`) and routes responses back
through the same connection, providing a single bidirectional channel.

## Pipeline Flow

### Control/Query path (full pipeline)

```
User: "把客厅灯调暗到30%"

Step 0 ─── Intent Classifier (tool-calling, temperature=0) ───
  Model: ChatOpenAI with classify_intent tool binding
  Input: "把客厅灯调暗到30%" + [方案A: recent conversation context] + [方案B: continuation hint]
  Output: tool_call("classify_intent", {"intent": "control", "rationale": "..."})

Step 1 ─── Intent Classifier (internal) ──────────────────
  Model: ChatOpenAI with classify_intent tool (temperature=0)
  Input: User message + 方案A context + 方案B continuation hint
  Output: {"intent": "control", "rationale": "..."}

Step 2 ─── Executor ────────────────────────────────────
  Prompt: EXECUTOR_PROMPT (tool execution)
  Input:  Plan from Step 1
  Tools:  ALL (list_devices, control_device, query_device, rag_retrieve, etc.)
  Action: control_device("light-001", "setBrightness", {"level": 30})
  Result: {"success": true, "device": "Parlor Light", ...}

Step 3 ─── Observer ────────────────────────────────────
  Prompt: OBSERVER_PROMPT (response formatting)
  Input:  Plan + Execution results + user message
  Tools:  NONE
  Output: "客厅灯已经调暗到30%了"
```

### Chat path (shortcut)

```
User: "你好"

Step 0 ─── Intent Classifier ────────────────────────────
  Output: {"intent": "chat", "rationale": "用户打招呼"}

Step 1 ─── Responder ───────────────────────────────────
  Prompt: RESPONDER_PROMPT (direct chat)
  Tools:  NONE
  Output: "你好！有什么可以帮你的？"
```

The Responder path saves one LLM round-trip compared to a full ReAct agent
(no tool-calling inference needed).

### Query path

```
User: "客厅温度多少？"

Step 0 ─── Intent Classifier → {"intent": "query", "rationale": "..."}
Step 1 ─── Executor → query_device("temp-001") → "25°C"
Step 2 ─── Observer → "客厅当前温度25°C"
```

### Perception path (rule-triggered, skip Planner)

```
Environment: ac-001 temperature drifts to 33.5°C

Step 1 ─── PerceptionEngine (pure Python, 0 LLM tokens)
  Rule: high_temperature (condition: currentTemperature > 32)
  Output: {"intent": "control",
           "rationale": "高温自动开启空调制冷",
           "steps": [{"action": "control_device", "target": "ac-001", ...}]}

         Plan emitted via EventBus (wot.perception.rule_triggered)

Step 2 ─── Executor (called via execute_plan_directly)
  Action: control_device("ac-001", "setMode", {"mode": "cool"})
          control_device("ac-001", "setTemperature", {"temp": 26})

Step 3 ─── Observer (response formatting)
  Output: "空调已自动开启制冷，目标温度26°C"
```

## Conditional Routing

Routing is determined by the `intent` field from the tool-calling classifier (Step 0):

| intent | Path | Tools | Use case |
|--------|------|-------|----------|
| `chat` | Intent Classifier → Responder | None | Greetings, general chat |
| `query` | Intent Classifier → Executor → Observer | All (read-only) | Device info, state queries |
| `control` | Intent Classifier → Executor → Observer | All | Device operations |
| *(rule-triggered)* | Executor → Observer directly | All | Automated environment response |

## Intent Continuity

The `ConversationState` class (in [`core/state.py`](../src/wotagent/core/state.py)) solves a common problem:
when a user follows up on a device control intent with a short answer, the follow-up gets
misclassified as `chat`.

```
User: "我很热"       → intent = control, agent asks "你在哪个房间？"
User: "我在客厅呢"   → WITHOUT state: intent = chat (misclassified)
                   → WITH state:    intent = control (continued)
```

Two mechanisms work together:

**方案A — Context injection:** Before intent classification, recent message pairs are injected
into the classifier prompt as "最近对话上下文". The LLM sees the full exchange history
and can correctly interpret short follow-ups.

**方案B — Continuation hint:** If the previous non-chat intent is still active and the agent
asked a question, a hint is injected: "⚠️ 提示：上一步的操作意图是「control」...".
This biases the classifier toward continuing the active intent.

Both are injected before the `classify_intent` tool call and cost zero extra LLM tokens
(they're part of the same call's context).

## Session Transcript

Each agent invocation can optionally produce dual JSONL transcript files for
fine-tuning and audit:

| File | Contents | Use case |
|------|----------|----------|
| `data/memory/{session_id}.full.jsonl` | All pipeline events: plan, tool calls, LLM I/O, timestamps, session metadata | Fine-tuning, detailed debugging |
| `data/memory/{session_id}.chat.jsonl` | User/assistant turns only | Chat log inspection |

Created automatically when `session_id` is provided to `invoke_agent_stream()`.
Events include: `session_start`, `system_prompt`, `user_message`, `llm_input`
(with full message list sent to the model), `plan`, `tool_call`, `assistant_response`,
`session_end`. Non-serializable objects (LangChain message types) are automatically
sanitized via `_sanitize()`.

## State Flow

```
                    AgentContext (shared across all phases)
                    ┌──────────────────────────────┐
                    │ session_id                   │
                    │ user_role                    │
                    │ user_message                 │
                    │ plan_json     ← set by       │ Planner / Perception
                    │ execution_results ← set by   │ Executor
                    │ perception_context ← set by  │ PerceptionEngine
                    └──────────────────────────────┘
```

Each phase receives the context with only the fields it needs. The `dynamic_prompt`
middleware reads from `AgentContext` to fill template variables.

## Perception Engine

See [PERCEPTION.md](PERCEPTION.md) for full design.

Key points:
- **Zero LLM tokens**: rule evaluation is pure Python comparisons
- **Background polling**: drifts simulated device states + evaluates rules
- **Injects context** into Planner system prompt for zero-tool-query awareness
- **Cooldown mechanism**: prevents rapid re-triggering (default 10 min)

## Single-Agent vs Multi-Agent

| Aspect | Single ReAct (legacy) | Multi-Agent Pipeline |
|--------|----------------------|---------------------|
| How it decides | LLM decides everything in one loop | Planner structures intent first |
| Tool access | All tools, LLM chooses | Planner: awareness only; Executor: full |
| Chat efficiency | LLM runs tool-calling loop anyway | Responder shortcut skips tools |
| Observability | One black-box stream | Each phase emits typed events |
| Failure recovery | LLM may or may not retry | Observer validates and reports |
| Plan auditability | None (implicit in LLM reasoning) | Plan is structured JSON, inspectable |
| Environment awareness | None (must query every time) | Perception Engine injects context proactively |
