# WoTAgent API Guide

## Base URLs

| Service | Base URL | Purpose |
|---------|----------|---------|
| Agent backend | `http://127.0.0.1:8000` | Chat pipeline, sessions, perception state, primary WebSocket stream |
| Frontend dev server | `http://localhost:5173` | Vue UI (proxies `/api` and `/ws` to backend) |
| Simulator platform | `http://127.0.0.1:18080` | Device simulation REST + simulator event stream |

## Startup Order

1. Simulator platform
2. Agent backend
3. Frontend

If you use one-click scripts:

```bat
scripts\start-simulator.bat
scripts\start-agent.bat
scripts\start-frontend.bat
```

## Agent Backend API (`:8000`)

### WebSocket (Primary realtime channel)

- Endpoint: `ws://127.0.0.1:8000/ws`
- Client commands:

| Type | Payload |
|------|---------|
| `chat` | `{"type":"chat","id":"cmd-1","data":{"message":"开客厅灯","session_id":"web_ui"}}` |
| `ping` | `{"type":"ping","id":"cmd-2"}` |

- Server messages:

| Type | Description |
|------|-------------|
| `response` | Acknowledge command receipt, includes `session_id` |
| `event` | Forwarded EventBus events (`event_type`, `data`, `session_id`, `timestamp`) |
| `pong` | Ping response |
| `error` | Command-level error payload |

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Service health |
| GET | `/api/diag` | Pipeline/session/device diagnostics |
| POST | `/api/chat` | Submit chat message (async processing) |
| POST | `/api/session` | Create session |
| GET | `/api/sessions` | List active sessions |
| DELETE | `/api/session/{session_id}` | Delete one session |
| GET | `/api/session/{session_id}/messages` | Load chat transcript entries |
| GET | `/api/devices` | List devices with current state |
| GET | `/api/perception/state` | Perception runtime state + device snapshot |
| GET | `/api/perception/rules` | Rule status list |
| POST | `/api/perception/poll` | Trigger one perception poll cycle |

### Example (REST)

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"把客厅灯调到30%","session_id":"web_ui","role":"operator"}'
```

## Simulator API (`:18080`)

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Simulator health |
| GET | `/api/td` | List all TD documents |
| GET | `/api/td/{device_id}` | Get one TD by id |
| GET | `/api/devices` | List simulator devices |
| GET | `/api/state` | Full simulator state + summary |
| GET | `/api/state/{device_id}` | State of one device |
| POST | `/api/control` | Execute device action |
| POST | `/api/poll` | Trigger one simulation poll |
| GET | `/api/events` | Read recent simulator events |
| POST | `/api/events/inject` | Inject manual event |
| POST | `/api/state/patch` | Patch single property value |
| POST | `/api/environment/set` | Batch set environment values |
| GET | `/api/weather/fetch` | Fetch weather data (`mode=shell|http`) |
| POST | `/api/weather/apply` | Apply weather to environment |

### Simulator WebSocket

- Endpoint: `ws://127.0.0.1:18080/ws/events`
- Behavior: publishes simulator-side event stream in JSON.

### Example (Simulator control)

```bash
curl -X POST http://127.0.0.1:18080/api/control \
  -H "Content-Type: application/json" \
  -d '{"device_id":"light-001","action":"setBrightness","parameters":{"level":30}}'
```

## Event Types Commonly Used by Frontend

| Event type | Meaning |
|------------|---------|
| `wot.agent.plan` | Intent classification result |
| `wot.agent.thought` | Model reasoning chunk |
| `wot.agent.token` | Response token chunk |
| `wot.agent.action.started` | Tool call started |
| `wot.agent.action.completed` | Tool call completed |
| `wot.agent.action.failed` | Tool call failed |
| `wot.agent.response` | Final response ready |
| `wot.session.started` | Session run start |
| `wot.session.ended` | Session run end |
| `wot.perception.rule_triggered` | Rule-triggered automation started |

## Troubleshooting

- Frontend cannot connect WS:
  - Verify backend is running on `127.0.0.1:8000`.
  - Check Vite proxy in `frontend/wot-vue/vite.config.ts`.
- `uv` command not found:
  - Install uv and reopen terminal.
- `OPENAI_API_KEY` missing:
  - Set it in `wotagent-prototype/.env` before starting backend.
