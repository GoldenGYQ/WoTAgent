<p align="center">
  <img src="https://img.shields.io/badge/LangChain-1.3%2B-2ea44f" alt="LangChain" />
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Vue.js-3-4FC08D" alt="Vue.js" />
  <img src="https://img.shields.io/badge/MCP-1.0-8B5CF6" alt="MCP" />
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="License" />
</p>

# WoTAgent

**Event-driven IoT Smart-Home Agent** — a multi-agent LLM system that understands natural language commands, controls IoT devices via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/), streams real-time events to a Vue dashboard, and runs automated environment monitoring — all with zero manual rule programming.

Built on **LangChain 1.3+** with a four-agent pipeline (Intent Classifier → Executor → Observer + Responder), **FastAPI** WebSocket server, and a **Vue 3** frontend.

---

## Architecture

```
               ┌──────────────────────┐
               │   Perception Engine  │ ←── Rule-based (0 LLM tokens)
               │   (background poll)  │
               └──────────┬───────────┘
                          │ triggered plan
                          ▼
User ──→ ┌─────────────────────────────────────────────────────┐
         │                    AgentPipeline                     │
         │  ┌──────────────────┐    ┌──────────────────┐        │
         │  │ Intent Classifier│───▶│    Executor      │control │
         │  │ (tool-calling,   │    │ (MCP tools)      │query   │
         │  │  temperature=0)  │    └────────┬─────────┘        │
         │  └────────┬─────────┘             │                  │
         │           │                       ▼                  │
         │           ├──chat──▶ Responder ───┼──▶ Observer      │
         │           │          (no tools)   │   (format reply) │
         └───────────┼───────────────────────┼──────────────────┘
                     ▼                       ▼
               EventBus (CloudEvents) ──► WebSocket ──► Vue Frontend
                                         └── SSE (legacy)
                                         └── Log
                                         └── Session Transcript
```

Two trigger paths:
- **User message** → Intent Classifier → Executor → Observer (or Responder for chat)
- **Rule triggered** → Executor → Observer directly (zero LLM for intent parsing)

---

## Quick Start

### 1. Backend

```bash
cd wotagent-prototype
uv venv
uv sync
cp .env.example .env   # Set your DeepSeek API key
```

Start the API server:
```bash
uv run --project . wotagent api
# Or: python -m wotagent.cli api
```

### 2. IoT Simulator (separate terminal)

```bash
cd wot-device-simulator
uv venv
uv sync
python -m wot_device_simulator.mcp_server
```

### 3. Frontend (separate terminal)

```bash
cd frontend/wot-vue
npm install
npm run dev
```

Open `http://localhost:5173` — the dashboard connects to the backend via WebSocket automatically.

### 4. Or try the REPL

```bash
uv run --project . wotagent repl
```

---

## Features

| Feature | Description |
|---------|-------------|
| **🧠 Multi-Agent Pipeline** | Intent Classifier → Executor → Observer: each phase has a focused role, producing typed CloudEvents at every step |
| **💬 Natural Language Control** | "把客厅灯调到30%" → automatic intent classification → device control → confirmation |
| **🔄 Intent Continuity** | Follow-ups like "我在客厅呢" inherit the previous control intent via 方案A (context injection) + 方案B (continuation hint) |
| **📡 Real-Time Streaming** | All agent actions stream as CloudEvents via WebSocket to the Vue frontend — thinking, tool calls, results |
| **📋 Session Transcripts** | Dual JSONL logs (full pipeline events + chat-only) at `data/memory/{session_id}.{full,chat}.jsonl` |
| **🔍 RAG-Enhanced Discovery** | ChromaDB vector search over device documentation — the agent can find devices by semantic query |
| **🌡️ Perception Engine** | Rule-based environment monitoring loop (0 LLM tokens) — auto-responds to conditions like high temperature |
| **🔐 RBAC** | Three-tier access control: `viewer` (read-only) / `operator` (control) / `admin` (full access) |
| **🔌 MCP Device Protocol** | IoT devices exposed via Model Context Protocol — the agent uses the same pattern as MCP tools |

---

## Project Structure

```
WoTAgent/
├── wotagent-prototype/          # Backend (Python)
│   ├── src/wotagent/
│   │   ├── api/app.py           # FastAPI + WebSocket + SSE
│   │   ├── core/                # Multi-agent pipeline, session, state
│   │   ├── events/              # EventBus, CloudEvents schema
│   │   ├── tools/               # IoT tools, MCP client, system tools
│   │   ├── perception/          # Rule engine, environment polling
│   │   ├── memory/              # Conversation memory, session transcripts
│   │   ├── prompts/             # LangChain prompt templates
│   │   ├── auth/rbac.py        # Role-based access control
│   │   ├── wot/td.py           # Thing Description loader
│   │   ├── rag/retriever.py    # ChromaDB vector retrieval
│   │   └── cli.py              # CLI entry point
│   ├── doc/                    # Architecture, pipeline, events docs
│   └── data/memory/            # Persisted conversations
├── wot-device-simulator/        # IoT simulation service
│   └── src/wot_device_simulator/
│       ├── mcp_server.py       # MCP tools (control, query, patch)
│       ├── simulator.py        # SQLite-backed device states
│       └── service.py          # REST API on port 18080
└── frontend/wot-vue/           # Vue 3 + Vite dashboard
    └── src/
        ├── components/         # DeviceDashboard, MessageItem, EventLog
        ├── views/ChatView.vue  # Main chat + dashboard layout
        ├── stores/chat.ts      # Pinia store (messages, sessions)
        └── api/client.ts       # WebSocket client + REST wrapper
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | DeepSeek / OpenAI API key |
| `OPENAI_BASE_URL` | `https://api.deepseek.com/v1` | API base URL |
| `LLM_MODEL` | `deepseek-chat` | Model name |
| `ENABLE_THINKING` | `true` | DeepSeek chain-of-thought |
| `ENABLE_PERCEPTION` | `true` | Start perception engine |
| `PERCEPTION_INTERVAL` | `60` | Polling interval (seconds) |
| `MCP_TRANSPORT_MODE` | `stdio` | MCP transport (`stdio` / `http`) |
| `MCP_SERVER_URL` | `http://localhost:8000/mcp` | MCP HTTP server URL |

---

## Documentation

| Document | Contents |
|----------|---------|
| [Architecture](doc/ARCHITECTURE.md) | System design, modules, routing, event transport |
| [Pipeline](doc/PIPELINE.md) | Multi-agent pipeline flow, streaming, transcript |
| [Events](doc/EVENTS.md) | CloudEvents schema, event types, WebSocket protocol |
| [Perception](doc/PERCEPTION.md) | Rule engine, polling loop, cooldown mechanism |

---

## How It Works

### Intent Classification

Before any agent runs, a lightweight tool-calling LLM call (temperature=0, no thinking) classifies the user's intent:

- **chat** → Responder (no tools, direct reply)
- **control** → Executor + Observer (full tool access)
- **query** → Executor + Observer (read-only tools)

This saves one LLM round-trip for simple conversations and ensures device commands are always routed correctly.

### Multi-Agent Pipeline

```
User: "把客厅灯调暗到30%"

Intent Classifier (temp=0)
  └→ {"intent": "control", "rationale": "用户想要调暗客厅灯"}

Executor (all tools)
  └→ control_device("light-001", "setBrightness", {"level": 30})
  └→ {"success": true}

Observer (response formatting)
  └→ "客厅灯已经调暗到30%了"
```

### Real-Time Events

Every agent action produces a typed CloudEvent on the EventBus, which is forwarded to the Vue frontend via WebSocket:

```
wot.agent.plan          → plan_update event
wot.agent.thought       → thinking indicator
wot.agent.action.started → tool call UI
wot.agent.action.completed → tool result
wot.agent.response      → final message
```

### Perception Automation

The Perception Engine runs a background polling loop — it drifts simulated device states and evaluates rules (pure Python, zero LLM tokens). When a rule matches, it triggers `execute_plan_directly()` which runs Executor → Observer, exactly like a user command but without the LLM intent-classification step.
