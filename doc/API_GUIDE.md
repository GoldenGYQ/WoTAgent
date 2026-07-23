# WoTAgent 2.0 API Guide

Status: current API surface

Last verified against routers: 2026-07-23

Base URL: `http://127.0.0.1:8000`.

The FastAPI application reports version `3.0.0`; the `/api/v2` prefix is kept
as the stable experiment API namespace and is not the product release number.

## Service

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service metadata |
| GET | `/api/health` | Health and active pipeline |
| GET | `/api/v2/capabilities` | Thinking, streaming and Agent capabilities |
| GET | `/api/v2/agents` | Registered specialist Agent metadata |
| GET | `/api/v2/prompts` | Active prompt profile metadata |

## Deployment Profiles

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v2/deployment/profiles` | List scene/profile definitions |
| PUT | `/api/v2/deployment/active` | Activate the same Profile in backend and simulator-facing runtime |

A Run persists `profile_id`, `domain_id` and `site_id`. One Conversation cannot
mix Profiles.

## Principals And Sessions

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v2/principals` | List visible Principals |
| GET | `/api/v2/principals/{id}/context` | Resolve roles, relationships, capabilities and resources |
| GET | `/api/v2/principals/role-templates` | List backend-owned registration templates |
| GET | `/api/v2/principals/registration-targets` | List valid resource targets |
| POST | `/api/v2/principals/register` | Persist a PIN-protected Principal from a template |
| POST | `/api/v2/principals/login` | Create a bearer session |
| GET | `/api/v2/principals/session` | Resolve the current bearer session |

The code also retains `/users` compatibility endpoints in the Principal router.
New clients should use the template-based registration and session endpoints.
Seeded demo identities support one-click use; registered identities must supply
their bearer session. This remains experiment-grade identity, not a production
authentication service.

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
| GET | `/api/v2/runs/{run_id}/trace-artifacts/{artifact_id}` | Fetch one on-demand raw trace artifact |

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

## Domain Operations

The generic Domain Operations API powers both eldercare and smart-home/villa
views:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v2/domains` | List registered domain manifests |
| GET | `/api/v2/domains/{domain_id}/subjects` | List domain subjects |
| GET | `/api/v2/domains/{domain_id}/subjects/{subject_id}/overview` | Read operational summary |
| GET | `/api/v2/domains/{domain_id}/subjects/{subject_id}/resources` | Read protected resources/devices |
| GET | `/api/v2/domains/{domain_id}/subjects/{subject_id}/events` | Read risk/operational events |
| GET | `/api/v2/domains/{domain_id}/subjects/{subject_id}/actions` | Read action/audit records |
| GET | `/api/v2/domains/{domain_id}/monitor/status` | Read deterministic Monitor status |
| POST | `/api/v2/domains/{domain_id}/telemetry` | Submit domain telemetry |
| POST | `/api/v2/domains/{domain_id}/scenarios` | Trigger an experiment scenario |
| POST | `/api/v2/domains/{domain_id}/events/{event_id}/acknowledge` | Acknowledge an event |
| POST | `/api/v2/domains/{domain_id}/events/{event_id}/status` | Apply a valid event-state transition |

The `/api/v2/eldercare/*` router remains as a domain-specific compatibility and
experiment surface. New cross-domain frontend work should prefer
`/api/v2/domains/*`.

## Speech

```text
POST /api/v2/speech/transcriptions
Content-Type: multipart/form-data
field: audio
```

The endpoint accepts WebM, M4A/MP4, MP3, OGG and WAV up to the configured byte
limit. It returns transcription text for the chat composer; it does not create
a Run automatically.

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

Principal/session and domain data use their own SQLite stores configured by the
runtime container. RunEvents are durable; the in-process WebSocket condition is
only a wake-up mechanism, so multi-instance notification is not yet supported.

## Removed APIs

The old `/api/v2/tasks`, `/api/v2/traces`, SSE Task stream and v1 routes are not
part of the Run/Task runtime.
