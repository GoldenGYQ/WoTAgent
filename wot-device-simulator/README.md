# wot-device-simulator

[English](README.md) | [简体中文](README.zh-CN.md)

A standalone IoT device simulator for agent development. All services run on a **single FastMCP server** on port `:18080` — MCP tools, REST endpoints, and dashboard in one process.

---

## Architecture

```
┌───────────────────────────────────────────────────────┐
│  wot-device-simulator  (port 18080)                   │
│                                                       │
│  ┌───────────────────────────────────────────────┐    │
│  │  SimulatorEngine (drift, rules, events)        │    │
│  │  DeviceStateStore (SQLite)                     │    │
│  └──────────────────┬────────────────────────────┘    │
│                     │                                 │
│     ┌───────────────┴───────────────────────┐          │
│     │                                       │          │
│     ▼                                       ▼          │
│  ┌─────────────────┐  ┌─────────────────────────┐     │
│  │  FastMCP Server  │  │  REST Endpoints          │     │
│  │  /mcp (MCP HTTP) │  │  /api/state, /api/events │     │
│  │  Agent controls  │  │  /api/control, /api/poll │     │
│  │  Agent queries   │  │  /api/weather/...        │     │
│  └─────────────────┘  ├─────────────────────────┤     │
│                        │  Dashboard               │     │
│                        │  /dashboard (HTML UI)    │     │
│                        └─────────────────────────┘     │
└───────────────────────────────────────────────────────┘
```

### How agent connects

| Transport | Agent config | Connection |
|-----------|-------------|------------|
| **stdio** (default) | `MCP_TRANSPORT_MODE=stdio` | Agent auto-spawns `mcp_server.py` as subprocess |
| **HTTP** (production) | `MCP_TRANSPORT_MODE=http` + `MCP_SERVER_URL=http://<ip>:18080/mcp` | POST to this server's `/mcp` endpoint |

---

## Quick Start

```bash
# Install
cd wot-device-simulator
uv venv && uv sync

# Start (one command does it all)
wot-sim-api
# Or: uv run --project . wot-sim-api
```

Then open:
- **Dashboard:** `http://127.0.0.1:18080/dashboard`
- **MCP endpoint:** `http://127.0.0.1:18080/mcp` (for agents)

---

## Services on Port 18080

### MCP Tools (for Agent, at `/mcp`)

| Tool | Description |
|------|-------------|
| `<device>__read_state` | TD-generated state reader with properties declared by that TD |
| `<device>__action__<action>` | TD-generated action tool whose input schema is copied from the TD |
| `list_devices` | List all devices with metadata (ID, title, location, capabilities) |
| `query_all_states` | Get runtime state for ALL devices in one call |
| `query_state(device_id)` | Get runtime state for one device |
| `control_device(device_id, action, params)` | Low-level compatibility and simulator-debug action bus; v2 Agent execution uses generated tools |
| `drift()` | Trigger one environmental drift cycle |
| `poll_once()` | Evaluate rules and return triggered ones |
| `patch_state(device_id, property, value)` | Directly set a device property |
| `set_environment(temperature, humidity, ...)` | Set environmental conditions |
| `inject_event(type, data)` | Inject a custom event |
| `get_td(device_id)` | Get Thing Description for a device |
| `download_all_td()` | Get all Thing Descriptions |

### REST Endpoints (for Dashboard & Manual Use)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Server health + engine status |
| `GET` | `/api/devices` | List all devices (TD metadata) |
| `GET` | `/api/state` | Full simulator state + all device runtime states |
| `GET` | `/api/state/{device_id}` | Single device runtime state |
| `POST` | `/api/control` | Control a device (`{"device_id", "action", "parameters"}`) |
| `POST` | `/api/poll` | Trigger one poll cycle |
| `GET` | `/api/events?limit=50` | Recent event history |
| `POST` | `/api/events/inject` | Inject a custom event |
| `POST` | `/api/state/patch` | Set a single device property |
| `POST` | `/api/environment/set` | Batch-set temperature/humidity/pm25/gasLevel |
| `GET` | `/api/weather/fetch?city=beijing` | Fetch live weather |
| `POST` | `/api/weather/apply` | Fetch weather and apply to simulation |

### Dashboard

- **URL:** `http://127.0.0.1:18080/dashboard`
- Real-time device cards with sliders, toggles, and quick actions
- Environment controls (temperature, humidity, pm2.5, gas)
- Weather fetch & apply
- Event injection
- Event feed (auto-polls every 3s)

---

## Internal Architecture

```
src/wot_device_simulator/
├── service.py           # FastMCP HTTP server — entry point for wot-sim-api
├── mcp_server.py        # MCP tool definitions (shared with agent stdio mode)
├── simulator.py         # DeviceStateStore (SQLite) + SimulatorEngine (drift, rules, events)
├── state_cli.py         # CLI for direct SQLite inspection
├── dashboard.html       # Simulator control panel UI
└── td.py                # Thing Description loader & discovery
```

### File Responsibilities

| File | Role | Key exports |
|------|------|-------------|
| `service.py` | HTTP server entry point | `main()` — starts FastMCP with HTTP transport, registers REST endpoints via `@mcp.custom_route()` |
| `mcp_server.py` | MCP tool definitions | `_build_server(sim)` — creates FastMCP instance with all IoT tools |
| `simulator.py` | Core simulation logic | `DeviceStateStore` (SQLite CRUD), `SimulatorEngine` (drift, rules, event pub/sub), `control_device()` |
| `state_cli.py` | Direct SQLite inspection | CLI to query/reset device state |
| `dashboard.html` | Control panel | Single-file HTML/JS dashboard (auto-polls REST APIs, no WebSocket) |
| `td.py` | Device metadata | `find_devices()`, `get_device_by_id()`, `load_tds()` from JSON files |

### Data Flow

```
MCP call or REST request
       │
       ▼
  service.py (route handler)
       │
       ├─ MCP tool call → simulator.py (control_device / drift / patch_state)
       ├─ REST /api/state → simulator.py (DeviceStateStore.get_all())
       ├─ REST /api/control → simulator.py (control_device)
       └─ REST /api/poll → simulator.py (poll_once → drift + rules)
                              │
                              ▼
                         DeviceStateStore
                         (SQLite at data/state.db)
                              │
                              ▼
                         Event published to subscribers
                         (dashboard auto-polls events via GET /api/events)
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `WOT_SIM_TD_ROOT` | `data/td/` | Local TD directory |
| `WOT_SIM_TD_REGISTRY` | — | Remote TD registry URL |

### Agent-side (for remote deployment)

In `wotagent-prototype/.env`:

```bash
MCP_TRANSPORT_MODE=http
MCP_SERVER_URL=http://<simulator-ip>:18080/mcp
```

---

## Development Notes

- **There is no `wot-sim-mcp` CLI command.** `mcp_server.py` is an internal module imported by `service.py` and auto-spawned by the agent in stdio mode. Users do not run it manually.
- **There is no FastAPI wrapper.** `service.py` uses pure FastMCP with `@mcp.custom_route()` for REST endpoints.
- **Dashboard uses HTTP polling** (3s interval), not WebSocket.
- All state is persisted in `data/state.db` (SQLite, WAL mode).
