# WoTAgent 2.0 API Guide

Base URL: `http://127.0.0.1:8000`.

## Service

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service metadata |
| GET | `/api/health` | Health and active pipeline |
| GET | `/api/v2/capabilities` | Thinking, streaming and Agent capabilities |

## Conversations

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v2/conversations` | Create a persistent conversation |
| GET | `/api/v2/conversations` | List conversations |
| GET | `/api/v2/conversations/{id}` | Read conversation metadata |
| GET | `/api/v2/conversations/{id}/turns` | Read persisted turns |
| DELETE | `/api/v2/conversations/{id}` | Delete a conversation |

## Runs And Tasks

One user query creates one Run. The Supervisor may create multiple dependent or
parallel Tasks under that Run; each professional Agent execution is recorded as
an AgentRun.

```http
POST /api/v2/runs
Content-Type: application/json

{
  "conversation_id": "conv_123",
  "message": "检查照明，并在需要时调节客厅环境",
  "role": "operator",
  "user_id": "local-user",
  "thinking": false
}
```

The API returns `202 Accepted` with a `run_id` and `turn_id`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v2/runs/{run_id}` | Read Run status and final response |
| GET | `/api/v2/runs/{run_id}/tasks` | List the Run's Task DAG |
| GET | `/api/v2/runs/{run_id}/tasks/{task_id}/agent-runs` | List Agent attempts |
| POST | `/api/v2/runs/{run_id}/tasks/{task_id}/confirm` | Resume a waiting Task |
| GET | `/api/v2/runs/{run_id}/events?after=N` | Replay ordered Run events |

Confirmation body:

```json
{"approved": true}
```

Run metadata, Tasks, AgentRuns, ordered events and pending confirmation payloads
are persisted in SQLite. Device subgraphs use the same `task_id` as their
LangGraph `thread_id`, so confirmation can resume the original checkpoint after
an application restart.

## WebSocket Events

Connect to `ws://127.0.0.1:8000/ws/v2`, then send:

```json
{"run_id":"run_123","after":0}
```

The server replays events in sequence order. Events include Supervisor
decisions, Task creation, Agent execution, confirmation and final Run state.
The stream sends `stream.completed` after a terminal Run status. Private
chain-of-thought is deliberately not exposed.

## Status Values

Run: `accepted`, `running`, `waiting_confirmation`, `completed`,
`partial_success`, `failed`, `cancelled`.

Task: `pending`, `ready`, `running`, `waiting_confirmation`, `completed`,
`failed`, `blocked`, `cancelled`.

## Persistence Configuration

```dotenv
WOTAGENT_CONVERSATION_DB=./data/conversations.db
WOTAGENT_RUN_DB=./data/agent_runs.db
WOTAGENT_CHECKPOINT_DB=./data/agent_checkpoints.db
```

`WOTAGENT_RUN_DB` stores API orchestration state. `WOTAGENT_CHECKPOINT_DB`
stores LangGraph node state and native interrupts. Both are required for
restart-safe confirmation recovery.

## Removed APIs

The old `/api/v2/tasks`, `/api/v2/traces`, SSE Task stream and v1 routes are not
part of the Run/Task runtime.
