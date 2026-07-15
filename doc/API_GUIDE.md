# WoTAgent 2.0 API Guide

Base URL: `http://127.0.0.1:8000`.

## Service

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service metadata |
| GET | `/api/health` | Health and active pipeline |
| GET | `/api/v2/capabilities` | Thinking and streaming capability |

## Conversations

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v2/conversations` | Create a persistent conversation |
| GET | `/api/v2/conversations` | List conversations |
| GET | `/api/v2/conversations/{id}` | Read conversation metadata |
| GET | `/api/v2/conversations/{id}/turns` | Read persisted turns |
| DELETE | `/api/v2/conversations/{id}` | Delete a conversation |

## Tasks

Create a task:

```http
POST /api/v2/tasks
Content-Type: application/json

{
  "conversation_id": "conv_123",
  "message": "把客厅灯调到 45%",
  "role": "operator",
  "thinking": false
}
```

The API returns `202 Accepted` with a `task_id`. Processing continues through FastAPI `BackgroundTasks`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v2/tasks/{task_id}` | Task status, result, usage and loop metrics |
| POST | `/api/v2/tasks/{task_id}/confirm` | Resume a checkpoint with `{ "approved": true }` |
| GET | `/api/v2/traces/{task_id}` | Trace record alias |
| GET | `/api/v2/tasks/{task_id}/events?after=N` | Ordered event replay |
| GET | `/api/v2/tasks/{task_id}/events/stream?after=N` | SSE trace stream |

## WebSocket Trace

Connect to `ws://127.0.0.1:8000/ws/v2`, then send:

```json
{"task_id":"task_123","after":0}
```

The server replays ordered events and ends with `stream.completed`. Events expose model request metadata, node transitions, tool input/output, policy decisions, verification, loop/recovery counts, Token usage, TTFT and estimated cost. Private chain-of-thought is deliberately not exposed.

## Status Values

- `accepted`, `running`
- `waiting_confirmation`
- `responded` for ordinary conversation
- `succeeded`, `partial_success`
- `denied`, `failed`, `cancelled`

## Removed v1 Routes

`/api/chat`, `/api/session*`, `/api/perception/*`, `/api/devices`, and `/ws` belonged to the 1.0 runtime and are intentionally absent from 2.0.
