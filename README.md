<p align="center">
  <img src="https://img.shields.io/badge/LangChain-1.3%2B-2ea44f" alt="LangChain" />
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Vue.js-3-4FC08D" alt="Vue.js" />
  <img src="https://img.shields.io/badge/MCP-1.0-8B5CF6" alt="MCP" />
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="License" />
</p>

# WoTAgent

[English](README.md) | [简体中文](README.zh-CN.md)

**Event-driven IoT Smart-Home Agent** — control your devices with natural language, and let the AI understand, plan, and execute. Say "set the living room light to 30%" or "I'm hot" — the Agent handles the rest.

Built on **LangChain 1.3+** with a three-agent pipeline (Intent Classifier → Executor → Observer + Responder shortcut), a **FastAPI** WebSocket server, and a **Vue 3** dashboard.

### Key Capabilities

- **Natural language control**: "set the living room light to 30%" — the Agent classifies the intent, locates the device, executes the action, and returns a structured confirmation
- **Multi-turn conversation**: "I'm hot" → Agent asks which room → "living room" — it correctly continues the AC control intent instead of drifting into casual chat
- **Real-time visibility**: The frontend streams every step — intent classification, tool calls, execution results — nothing happens in a black box
- **Dual-mode deployment**: stdio mode auto-spawns the simulator during development (one terminal, zero config); HTTP mode connects to a remote simulator in production
- **Automated perception**: Background polling evaluates device states against rules — pure Python, zero LLM tokens. High temperature? Turn on the AC. Low humidity? Start the humidifier.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        WoTAgent Monorepo                             │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  wotagent-prototype  (Agent Backend)                          │   │
│  │  Port :8000, WebSocket, LLM pipeline                          │   │
│  │                                                                │   │
│  │  User → Intent Classifier → Executor → Observer → Response    │   │
│  │         (chat) → Responder shortcut                            │   │
│  │                     │                                          │   │
│  │               EventBus → WebSocket → Vue Frontend              │   │
│  │               EventBus → Log / SessionTranscript               │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                              │ MCP (stdio or HTTP)                    │
│  ┌──────────────────────────▼───────────────────────────────────┐   │
│  │  wot-device-simulator  (IoT Simulator)                         │   │
│  │  Port :18080, FastMCP + REST + Dashboard                      │   │
│  │                                                                │   │
│  │  ┌─────────────┐  ┌──────────────────┐  ┌────────────────┐   │   │
│  │  │ MCP Tools    │  │ REST Endpoints   │  │ Dashboard      │   │   │
│  │  │ (via /mcp)   │  │ (/api/*)         │  │ (/dashboard)   │   │   │
│  │  └──────┬──────┘  └──────────────────┘  └────────────────┘   │   │
│  │         │                                                     │   │
│  │  ┌──────▼─────────────────────────────────────────────────┐   │   │
│  │  │  DeviceStateStore (SQLite) + SimulatorEngine (drift)    │   │   │
│  │  └────────────────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌──────────────────────────────────────────────┐                   │
│  │  frontend/wot-vue  (Vue 3 Dashboard)          │                   │
│  │  Port :5173, WebSocket → Agent                │                   │
│  └──────────────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────┘
```

### Two Deployment Modes

| Mode | Agent MCP config | Simulator | Use case |
|------|-----------------|-----------|----------|
| **stdio** (default) | `MCP_TRANSPORT_MODE=stdio` | **No manual start needed** — Agent auto-spawns `mcp_server.py` as subprocess | Development on one machine |
| **HTTP** | `MCP_TRANSPORT_MODE=http` + `MCP_SERVER_URL=http://<ip>:18080/mcp` | Must run `wot-sim-api` manually | Production on separate machines |

> **💡 stdio mode deep-dive (recommended for development):**
>
> In stdio mode you only need **one terminal** — the Agent backend:
>
> ```bash
> # That's it — no need to start the simulator separately!
> wotagent api
> ```
>
> When the Agent starts, it **automatically spawns `mcp_server.py` as a subprocess** via FastMCP's stdio pipe. All device control commands flow through stdin/stdout — completely transparent to you. Open `http://localhost:5173` and you can start controlling devices immediately.
>
> **So when do I actually need `wot-sim-api`?**
>
> | What you want | What to do in stdio mode |
> |--------------|--------------------------|
> | Just control devices via Agent | ✅ `wotagent api` + frontend is enough |
> | See the simulator dashboard | Optional: also run `wot-sim-api` → `http://localhost:18080/dashboard` |
> | Production deployment | Switch to HTTP mode, simulator on another machine |
>
> ⚠️ **Common misconception:** "The simulator is a separate service, so I must start it first." **In stdio mode, you don't** — the Agent handles this for you. You only need to run `wot-sim-api` manually when using HTTP mode (cross-machine) or when you want the dashboard UI.

---

## Project Structure

```
WoTAgent/
│
├── scripts/                            # One-click startup scripts
│   ├── start-agent.{bat,sh}            # Start agent backend on :8000
│   ├── start-frontend.{bat,sh}         # Start Vue dashboard on :5173
│   └── start-simulator.{bat,sh}        # Start simulator on :18080
│
├── wotagent-prototype/                  # Agent Backend (Python)
│   ├── src/wotagent/
│   │   ├── cli.py                      # CLI entry: repl, api, log
│   │   ├── api/app.py                  # FastAPI + WebSocket server (:8000)
│   │   ├── core/
│   │   │   ├── agent.py                # Multi-agent pipeline (Intent Classifier → Executor → Observer + Responder)
│   │   │   ├── session.py              # Session lifecycle & memory linkage
│   │   │   └── state.py                # ConversationState — cross-turn intent continuity
│   │   ├── events/
│   │   │   ├── schema.py               # CloudEvents 1.0 format
│   │   │   └── bus.py                  # Pub/sub event bus with history replay
│   │   ├── prompts/
│   │   │   └── templates.py            # LangChain prompt templates (5 roles)
│   │   ├── tools/
│   │   │   ├── iot.py                  # IoT tools: control_device, query_device, list_devices (via MCP)
│   │   │   ├── mcp_client.py           # MCP client (stdio auto-spawn / HTTP remote)
│   │   │   └── registry.py             # System tools (psutil, file ops)
│   │   ├── perception/
│   │   │   ├── engine.py               # PerceptionEngine — rule evaluation via MCP (no local SQLite)
│   │   │   └── rules.py                # Condition, EnvironmentRule models + defaults
│   │   ├── memory/
│   │   │   └── manager.py              # ConversationMemory + SessionTranscript (dual JSONL)
│   │   ├── wot/td.py                   # Thing Description loader & discovery
│   │   ├── rag/retriever.py            # ChromaDB vector search for TD docs
│   │   ├── auth/rbac.py                # Role-based access control (admin/operator/viewer)
│   │   └── logging/logger.py           # Structured logging with EventBus integration
│   ├── doc/                            # Detailed architecture documentation
│   │   ├── ARCHITECTURE.{md,zh-CN.md}
│   │   ├── PIPELINE.{md,zh-CN.md}
│   │   ├── EVENTS.{md,zh-CN.md}
│   │   └── PERCEPTION.{md,zh-CN.md}
│   ├── tests/
│   │   ├── test_core.py
│   │   └── test_perception.py
│   └── data/memory/                   # Persisted conversation transcripts
│
├── wot-device-simulator/               # IoT Simulator (Python)
│   ├── src/wot_device_simulator/
│   │   ├── service.py                 # FastMCP HTTP server (:18080) — MCP + REST + Dashboard in one process
│   │   ├── mcp_server.py              # MCP tool definitions (used by service.py and agent stdio mode)
│   │   ├── simulator.py               # DeviceStateStore (SQLite) + SimulatorEngine (drift, rules)
│   │   └── dashboard.html             # Simulator control panel
│   ├── data/td/                       # Thing Description JSON files
│   └── README.{md,zh-CN.md}
│
├── frontend/wot-vue/                   # Vue 3 + Vite Frontend
│   ├── src/
│   │   ├── views/ChatView.vue         # Main chat + dashboard layout
│   │   ├── components/                # DeviceDashboard, MessageItem, EventLog
│   │   ├── stores/chat.ts             # Pinia store (messages, sessions)
│   │   └── api/client.ts              # WebSocket client + REST wrapper
│   └── README.md
│
├── CLAUDE.md                          # Project instructions for Claude Code
├── README.md                          # This file
└── README.zh-CN.md                    # Chinese version
```

---

## Quick Start

### One-Click Start

Start three terminals and run in order:

```bash
# Terminal 1: Simulator (IoT device simulation)
scripts/start-simulator.sh   # macOS / Linux
scripts\start-simulator.bat  # Windows

# Terminal 2: Agent backend
scripts/start-agent.sh       # macOS / Linux
scripts\start-agent.bat      # Windows

# Terminal 3: Frontend
scripts/start-frontend.sh    # macOS / Linux
scripts\start-frontend.bat   # Windows
```

Then open:
- **Frontend:** `http://localhost:5173`
- **Agent API:** `http://127.0.0.1:8000`
- **Simulator dashboard:** `http://127.0.0.1:18080/dashboard`
- **Agent API docs:** `http://127.0.0.1:8000/docs`

### Manual Start

```bash
# 1. Setup both projects
cd wot-device-simulator && uv venv && uv sync
cd ../wotagent-prototype && uv venv && uv sync
cp .env.example .env   # Set your DeepSeek/OpenAI API key

# 2. Start simulator
cd wot-device-simulator
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
wot-sim-api               # → http://127.0.0.1:18080

# 3. Start agent (new terminal)
cd wotagent-prototype
source .venv/bin/activate
wotagent api              # → http://127.0.0.1:8000

# 4. Start frontend (new terminal)
cd frontend/wot-vue
npm install && npm run dev  # → http://localhost:5173

# 5. Or try the REPL
cd wotagent-prototype
source .venv/bin/activate
wotagent repl
```

> **Note:** In development mode (`MCP_TRANSPORT_MODE=stdio`, the default), the
> agent automatically spawns the simulator's MCP server as a subprocess — the
> simulator terminal is optional but recommended for the dashboard.

---

## How the Agent Pipeline Works

### Intent Classification

Before any agent runs, a lightweight tool-calling LLM call (temperature=0, no thinking) classifies the user's intent:

```
User: "把客厅灯调暗到30%"

Intent Classifier (temp=0, tool-calling)
  └→ classify_intent() → {"intent": "control", "rationale": "用户想要调暗客厅灯"}

Routing based on intent:
  chat     → Responder (no tools, direct reply) — saves LLM round-trips
  control  → Executor + Observer (full tool access)
  query    → Executor + Observer (read-only tools)
```

### Full Pipeline (Control Intent)

```
User: "把客厅灯调暗到30%"

Step 1 — Intent Classifier (temperature=0, no thinking)
  └→ {"intent": "control", "rationale": "用户想调暗客厅灯"}

Step 2 — Executor (all tools available)
  └→ MCP call: control_device("light-001", "setBrightness", {"level": 30})
     │
     ├─ stdio mode:  Auto-spawns mcp_server.py as subprocess
     │                → writes to DeviceStateStore (SQLite)
     │                → returns new state
     │
     └─ HTTP mode:   HTTP POST to simulator's /mcp endpoint
                      → simulator processes locally
                      → returns new state
  └→ {"success": true, "device": "Parlor Light", "state": {...}}

Step 3 — Observer (response formatting, no tools)
  └→ "客厅灯已经调暗到30%了"

All steps emit CloudEvents → EventBus → WebSocket → Vue Frontend
```

### Intent Continuity

The system tracks conversation context across turns via `ConversationState`:

```
User: "我很热"       → intent = control, agent asks "你在哪个房间？"
User: "我在客厅呢"   → WITHOUT state: intent = chat (misclassified)
                   → WITH state:    intent = control (continued via 方案A + 方案B)
```

### Perception Automation

The **PerceptionEngine** runs a background loop that:
1. Calls the simulator's `drift` MCP tool (simulates temperature/humidity changes)
2. Fetches all device states via `query_all_states` MCP tool
3. Evaluates rules locally (pure Python, zero LLM tokens)
4. Emits `wot.perception.rule_triggered` events when conditions match

```
PerceptionEngine (background, every 60s)
  │
  ├─ MCP call: drift() → simulator randomizes sensor values
  ├─ MCP call: query_all_states() → get current device states
  │
  └─ Evaluate rules locally (Python)
       └─ If temp > 32°C → emit rule_triggered event
            └─ If ENABLE_PERCEPTION_AUTO_EXEC=true → run Executor → Observer
```

---

## MCP Transport: stdio vs HTTP

### stdio mode (default, development)

```
Agent process                  Simulator
┌─────────────────┐           ┌─────────────────────┐
│ MCPToolClient   │──stdio──→│ mcp_server.py        │
│ (get_mcp_client)│   pipe   │ (auto-spawned)       │
│                 │←─────────│ → DeviceStateStore   │
└─────────────────┘           └─────────────────────┘
```

- **Zero config** — Agent auto-spawns the simulator script as a subprocess
- **No network** — Communication is over stdin/stdout pipes
- **Fast** — No HTTP overhead, direct process-to-process
- The `wot-sim-api` is still useful for the dashboard view

### HTTP mode (production)

```
Agent machine                  Simulator machine
┌─────────────────┐  HTTP     ┌─────────────────────────┐
│ MCPToolClient   │─────────→│ wot-sim-api (:18080)     │
│ (get_mcp_client)│  POST    │ ┌─────────────────────┐  │
│                 │←─────────│ │ FastMCP HTTP server  │  │
└─────────────────┘          │ │ MCP + REST + UI     │  │
                              │ └─────────────────────┘  │
                              └─────────────────────────┘
```

- **Remote** — Agent and simulator can be on different machines
- **Scales** — Each is an independent service
- **Visible** — Simulator logs show every MCP call

---

## Simulator: Single-Port Architecture

The simulator runs a **single FastMCP HTTP server** on port `:18080` that serves:

| Path | Protocol | Purpose |
|------|----------|---------|
| `/mcp` | Streamable HTTP (MCP) | Agent device control/query |
| `/api/state` | REST | Current device states |
| `/api/control` | REST | Manual device control |
| `/api/events` | REST | Event history |
| `/api/weather/*` | REST | Weather fetch & apply |
| `/dashboard` | HTML | Simulator control panel |

> **Note:** `wot-sim-mcp` is an internal tool used only by the agent's stdio
> auto-spawn mechanism. Users do not need to run it manually.

---

## Environment Variables

### Agent (.env in wotagent-prototype/)

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | DeepSeek / OpenAI API key |
| `OPENAI_BASE_URL` | `https://api.deepseek.com/v1` | API base URL |
| `LLM_MODEL` | `deepseek-chat` | Model name |
| `ENABLE_THINKING` | `true` | DeepSeek chain-of-thought |
| `ENABLE_STREAMING` | `true` | Token streaming in REPL |
| `ENABLE_PERCEPTION` | `true` | Start perception engine |
| `PERCEPTION_INTERVAL` | `60` | Polling interval (seconds) |
| `ENABLE_PERCEPTION_AUTO_EXEC` | `false` | Auto-execute triggered rules (costs LLM tokens) |
| `MCP_TRANSPORT_MODE` | `stdio` | `stdio` (dev, auto-spawn) or `http` (prod, remote) |
| `MCP_SERVER_URL` | `http://localhost:18080/mcp` | Simulator MCP endpoint (HTTP mode only) |

### Simulator

| Variable | Default | Description |
|----------|---------|-------------|
| `WOT_SIM_TD_ROOT` | `data/td/` | Local TD directory |
| `WOT_SIM_TD_REGISTRY` | — | Remote TD registry URL |

---

## Documentation

| Document | EN | CN | Contents |
|----------|----|----|---------|
| Architecture | [EN](wotagent-prototype/doc/ARCHITECTURE.md) | [中文](wotagent-prototype/doc/ARCHITECTURE.zh-CN.md) | System design, modules, routing, event transport |
| Pipeline | [EN](wotagent-prototype/doc/PIPELINE.md) | [中文](wotagent-prototype/doc/PIPELINE.zh-CN.md) | Intent classification, role flow, streaming |
| Events | [EN](wotagent-prototype/doc/EVENTS.md) | [中文](wotagent-prototype/doc/EVENTS.zh-CN.md) | CloudEvents schema, types, WebSocket protocol |
| Perception | [EN](wotagent-prototype/doc/PERCEPTION.md) | [中文](wotagent-prototype/doc/PERCEPTION.zh-CN.md) | Rule engine, polling loop, cooldown mechanism |
| Simulator | [EN](wot-device-simulator/README.md) | [中文](wot-device-simulator/README.zh-CN.md) | Simulator setup, API, dashboard |

---

## Features

| Feature | Description |
|---------|-------------|
| **🧠 Multi-Agent Pipeline** | Intent Classifier → Executor → Observer: each phase has a focused role, producing typed CloudEvents at every step |
| **💬 Natural Language Control** | "把客厅灯调到30%" → automatic intent classification → device control → confirmation |
| **🔄 Intent Continuity** | Follow-ups like "我在客厅呢" inherit the previous control intent via 方案A (context injection) + 方案B (continuation hint) |
| **📡 Real-Time Streaming** | All agent actions stream as CloudEvents via WebSocket to the Vue frontend |
| **📋 Session Transcripts** | Dual JSONL logs (full pipeline events + chat-only) at `data/memory/{session_id}.{full,chat}.jsonl` |
| **🔍 RAG-Enhanced Discovery** | ChromaDB vector search over device documentation |
| **🌡️ Perception Engine** | Rule-based environment monitoring via MCP (0 LLM tokens for evaluation) |
| **🔐 RBAC** | Three-tier access: `viewer` (read-only) / `operator` (control) / `admin` (full) |
| **🔌 MCP Device Protocol** | IoT devices exposed via Model Context Protocol — dual transport (stdio/HTTP) |
| **📦 Cross-Platform** | Windows `.bat` + macOS/Linux `.sh` startup scripts |

---

## License

MIT
