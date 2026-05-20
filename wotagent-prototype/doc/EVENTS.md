# Event System

## CloudEvents Format

All agent events follow the [CloudEvents 1.0](https://cloudevents.io/) schema:

```json
{
  "specversion": "1.0",
  "id": "a1b2c3d4-e5f6-...",
  "source": "wotagent/core/pipeline",
  "type": "wot.agent.plan",
  "subject": "session/sess_abc123",
  "time": "2026-05-19T10:00:00Z",
  "session_id": "sess_abc123",
  "data": { "plan": {"intent": "control", ...} }
}
```

## Event Types

| Type | Producer | When |
|------|----------|------|
| `wot.session.started` | `invoke_agent_stream` / `execute_plan_directly` | Agent invocation begins |
| `wot.session.ended` | `invoke_agent_stream` / `execute_plan_directly` | Agent invocation ends |
| `wot.agent.plan` | Intent Classifier / Observer | Structured intent + plan produced |
| `wot.agent.thought` | LLM | DeepSeek reasoning_content (thinking mode) |
| `wot.agent.token` | Pipeline | Response text fragment (streaming) |
| `wot.agent.observation` | Executor / Observer | Validation output, tool observations |
| `wot.agent.action.started` | Executor | A tool call is initiated |
| `wot.agent.action.completed` | Executor | A tool call succeeds |
| `wot.agent.action.failed` | Executor | A tool call errors |
| `wot.agent.response` | Pipeline | Final response text |
| `wot.agent.error` | Pipeline | Agent invocation error |
| `wot.device.state_changed` | IoT tools | Device state change (simulated) |
| `wot.device.discovered` | WoT | New device found |
| `wot.perception.state` | PerceptionEngine | (reserved) Periodic environment snapshot |
| `wot.perception.rule_triggered` | PerceptionEngine | A rule condition matched and fired |
| `wot.system.log` | Various | Log messages forwarded to bus |

## Event Bus

The `EventBus` is a memory-based pub/sub:

```python
bus = get_bus()

# Subscribe
unsub = bus.subscribe(my_callback, event_type="wot.agent.plan")

# Emit
await bus.emit(Event(source="...", type="wot.agent.plan", data={...}))

# Unsubscribe
unsub()

# History replay
for evt in bus.history(start_cursor=42):
    print(evt)
```

- `max_history` (default 1000) limits retained events
- Each event gets a cursor for SSE replay
- SSE endpoint at `GET /api/events/{session_id}`

## Consumer: Logging

`install_event_logger()` subscribes to all `wot.*` events and writes them to
`wotagent.log`. Only high-level semantic events are logged (no `on_chain_start`
internal noise).

## Consumer: SessionTranscript

Each agent invocation can produce dual JSONL transcript files:

| File | Contents |
|------|----------|
| `data/memory/{session_id}.full.jsonl` | All pipeline events + LLM I/O |
| `data/memory/{session_id}.chat.jsonl` | User/assistant turns only |

See [PIPELINE.md](PIPELINE.md#session-transcript) for details.

## Consumer: WebSocket (primary)

The FastAPI endpoint `ws://localhost:8000/ws` streams events bidirectionally.
The server forwards all `EventBus` events to the WebSocket as JSON messages.

### WebSocket Protocol

**Client → Server commands:**

| Type | Data | Description |
|------|------|-------------|
| `chat` | `{"message": "...", "session_id": "..."}` | Send a chat message to the agent |
| `ping` | — | Keep-alive, server responds with `pong` |

**Server → Client messages:**

| Type | Fields | Description |
|------|--------|-------------|
| `event` | `event_type`, `data`, `session_id`, `timestamp` | Real-time agent event forwarded from EventBus |
| `response` | `id`, `success`, `data` | Acknowledges a `chat` command, provides `session_id` |
| `pong` | `id` | Response to `ping` |
| `error` | `id`, `message` | Error response to a command |

**Example chat flow:**
```json
→ {"type": "chat", "id": "cmd-001", "data": {"message": "开灯", "session_id": "sess_xxx"}}
← {"type": "response", "id": "cmd-001", "success": true, "data": {"session_id": "sess_xxx"}}
← {"type": "event", "event_type": "wot.agent.plan", "data": {...}, "session_id": "sess_xxx", "timestamp": ...}
← {"type": "event", "event_type": "wot.agent.action.started", "data": {...}, ...}
← {"type": "event", "event_type": "wot.agent.action.completed", "data": {...}, ...}
← {"type": "event", "event_type": "wot.agent.response", "data": {...}, ...}
← {"type": "event", "event_type": "wot.session.ended", "data": {}, ...}
```

The WebSocket is the **primary** transport for the Vue frontend. SSE endpoints
are retained for backward compatibility and dashboard use.

## Consumer: SSE (legacy, retained for compatibility)

The FastAPI endpoint `GET /api/events/{session_id}` streams events to frontend
clients using Server-Sent Events:

```
event: wot.agent.plan
data: {"specversion":"1.0",...,"data":{"plan":{...}}}

event: wot.agent.action.started
data: {"specversion":"1.0",...,"data":{"action":"control_device"}}
```

## Event Flow Diagram

### User-triggered path

```
User message
     │
     ▼
invoke_agent_stream()
     │
     ├── emit wot.session.started
     │
     ├── emit wot.agent.plan        ──┐
     │                                │
     ├── executor.astream_events()    ├── WebSocket → Frontend
     │   ├── emit action.started     │
     │   ├── emit action.completed   ├── SSE → Dashboard (legacy)
     │   └── emit action.failed      │
     │                               ├── Log → wotagent.log
     ├── emit wot.agent.response     │
     │                              ├── Transcript → .jsonl
     ├── emit wot.session.ended     ──┘
     │
     └── yield {"type": "done", ...}
```

### Perception-triggered path

```
Environment change (simulated drift)
     │
     ▼
PerceptionEngine.poll_once()
     │
     ├── DeviceStateStore.drift()
     │
     ├── _evaluate_rules()
     │   └── Condition.evaluate() → True
     │
     ├── emit wot.perception.rule_triggered  ──┐
     │    data: {rule, plan, value, threshold}  ├── SSE → Frontend
     │                                          │
     ▼                                          │
execute_plan_directly()                        │
     │                                          │
     ├── emit wot.session.started               │
     ├── executor.astream_events()             │
     │   ├── action.started                    │
     │   ├── action.completed                  │
     │   └── action.failed                     │
     ├── observer.ainvoke()                    │
     ├── emit wot.agent.response               │
     ├── emit wot.session.ended               ──┘
     └── return final_text
```
