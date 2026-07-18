# WoTAgent 2.0

WoTAgent 2.0 is an experiment-oriented LLM + Web of Things control system. The LLM converts natural-language requests into typed `TaskPlan` objects. Deterministic code owns RBAC, confirmation, execution, state verification, and bounded recovery. WoT Thing Descriptions are compiled into MCP tools at runtime. The eldercare scenario adds event-driven health monitoring, MCP health Resources, and a Butler supervisor that dispatches risk handling to Care and Environment subgraphs.

```text
Vue console -> FastAPI /api/v2 -> LangGraph agent loop
-> Planner -> Policy -> Orchestrator -> Verifier/Recovery -> Response Agent
-> MCP gateway -> TD-generated tools -> simulator / SimuHome / ESP32
```

## Components

- `wotagent-prototype`: Agent graph, API, policy, orchestration, trace, and evaluation.
- `frontend/wotagent-v2-console`: Dedicated Vue 3 trace console.
- `wot-device-simulator`: TD registry, MCP tools, REST API, dashboard, and device state.
- `wotagent-prototype/src/wotagent/eldercare`: Sensor store, scheduled monitor, Butler/subagent graphs, and the eldercare MCP server.

The v1 implementation remains available in Git history and tag `v1.0.0`. The current branch no longer carries the old ReAct runtime, EventBus, polling perception engine, legacy session memory, or legacy Vue frontend.

## Start

Recommended Windows entry points:

```text
start-wotagent.bat         Start the complete local stack
start-wotagent-safe.bat    Start without submitting Monitor events to the LLM
start-wotagent-remote.bat  Start the stack and open a temporary Cloudflare Quick Tunnel
status-wotagent.bat        Show process and health status
stop-wotagent.bat          Stop the stack and remove the Tailscale Serve proxy
```

The managed stack starts the simulator (`18080`), Agent API (`8000`),
independent Monitor worker, and Vue console (`5174`) in that order. Logs and
PID files are stored under `%LOCALAPPDATA%\WoTAgent\stack`, outside the
repository.

For phone access, run `start-wotagent-remote.bat`. It prints a temporary
`trycloudflare.com` URL and records the tunnel process so `stop-wotagent.bat`
can close it. This URL is public and the experimental API has no real
authentication, so use it only for short tests and stop it immediately after.

Private Tailscale access remains available from PowerShell with
`scripts\wotagent-stack.ps1 private`.

Manual commands remain available:

```powershell
# Simulator (optional when using stdio MCP)
cd wot-device-simulator
.\.venv\Scripts\python.exe -m wot_device_simulator.service

# Agent API
cd ..\wotagent-prototype
.\.venv\Scripts\python.exe -m wotagent.cli api --host 127.0.0.1 --port 8000

# Vue console
cd ..\frontend\wotagent-v2-console
npm install
npm run dev

# Eldercare MCP Resources and Tools (optional standalone service)
cd ..\..\wotagent-prototype
.\.venv\Scripts\python.exe -m wotagent.eldercare.mcp_server --transport http --port 18082
```

Backend docs: `http://127.0.0.1:8000/docs`. Simulator dashboard: `http://127.0.0.1:18080/dashboard`.

## Verify

```powershell
cd wotagent-prototype
.\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend\wotagent-v2-console
npm test
npm run build

cd ..\..\wot-device-simulator
.\.venv\Scripts\python.exe -m pytest -q
```

See [README.zh-CN.md](README.zh-CN.md), the [eldercare scenario guide](doc/ELDERCARE_SCENARIO.md), and the [2.0 technical guide](doc/WOTAGENT_2.0_COMPLETE_TECHNICAL_GUIDE.md).
