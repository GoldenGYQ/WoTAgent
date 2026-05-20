# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Architecture

Two main projects under a monorepo:

```
WoTAgent/
├── wotagent-prototype/    # Backend (Python) + Vue frontend
│   ├── src/wotagent/      # Python package
│   │   ├── api/app.py     # FastAPI + WebSocket + SSE endpoints
│   │   ├── core/agent.py  # Multi-agent pipeline (Intent→Planner→Executor→Observer→Responder)
│   │   ├── core/session.py # Session management
│   │   ├── core/state.py  # ConversationState — intent continuity across turns
│   │   ├── events/        # EventBus (pub/sub), CloudEvents schema
│   │   ├── tools/         # LangChain tools (IoT, MCP client, system)
│   │   ├── perception/    # Background environment simulation + rule engine
│   │   ├── memory/        # Session-scoped conversation memory + SessionTranscript
│   │   ├── prompts/       # LangChain ChatPromptTemplate definitions
│   │   ├── auth/rbac.py   # Role-based access control (admin/operator/viewer)
│   │   ├── wot/td.py      # Thing Description loading and discovery
│   │   └── cli.py         # CLI entry point (repl, api, log subcommands)
│   └── frontend/wot-vue/  # Vue 3 + Vite frontend
└── wot-device-simulator/  # Standalone IoT simulation service (MCP + REST)
    └── src/wot_device_simulator/
        ├── mcp_server.py  # MCP tools (control_device, query_state, etc.)
        ├── service.py     # FastAPI on port 18080 (REST fallback)
        ├── simulator.py   # DeviceStateStore (SQLite-backed), drift, rules
        └── state_cli.py   # CLI for direct SQLite state manipulation
```

**Agent pipeline flow:**
```
User message → Intent Classifier (tool-calling, temp=0) → Executor (MCP tools) → Observer (format response)
                 ├─ intent=chat ──→ Responder (no tools, direct reply)
                 └─ intent=control|query ──→ Executor → Observer

Intent continuity: ConversationState tracks non-chat intent across turns (方案A context injection + 方案B continuation hint)
SessionTranscript: dual JSONL logging (full + chat) for each session
```

**Event flow:** Agent actions → EventBus (CloudEvents format) → WebSocket/SSE → Frontend

**IoT tool chain:** Agent → `control_device()` / `list_devices()` / `query_device()` → MCP client → Simulation MCP server → DeviceStateStore

## Key Commands

**Backend:**
- `wotagent api` — Start FastAPI server on port 8000 (from `wotagent-prototype/`)
- `wotagent repl` — Interactive chat REPL
- `.venv/Scripts/python.exe -X utf8 -m wotagent.cli api` — Start API with UTF-8 mode (Windows)
- `.venv/Scripts/python.exe -m pytest tests/ -v` — Run tests

**Frontend:**
- `npm run dev` — Start Vite dev server (from `frontend/wot-vue/`)
- `npm run build` — Production build
- `npm run format` — Format with oxfmt

**Simulation:**
- `python -m wot_device_simulator.mcp_server` — Start MCP stdio server
- `python -m wot_device_simulator.service` — Start REST API on port 18080

## Environment

Copy `.env.example` to `.env`. Required: `OPENAI_API_KEY` (DeepSeek). Model defaults to `deepseek-chat`. Key env vars:

- `LLM_MODEL` — OpenAI-compatible model name
- `ENABLE_THINKING` — DeepSeek chain-of-thought (default: true)
- `MCP_TRANSPORT_MODE=http` — Use HTTP MCP transport (default: stdio)
- `MCP_SERVER_URL` — MCP HTTP server URL (default: http://localhost:8000/mcp)

## Important Conventions

- IoT tools are async (MCP-first) + sync (local fallback) via `StructuredTool.from_function(func=..., coroutine=...)`
- All agent actions produce CloudEvents via `EventBus` — frontend consumes these via WebSocket `onmessage`
- The perception engine imports `DeviceStateStore` from `wot-device-simulator` directly (shared SQLite state)
- MCP client connects to simulation project at `../wot-device-simulator/src/wot_device_simulator/mcp_server.py` by default
- Session memory persists to `data/memory/{session_id}.json`
- Session transcripts log to `data/memory/{session_id}.full.jsonl` and `.chat.jsonl`
- RBAC hierarchy: viewer < operator < admin
- Intent classification uses tool-calling LLM (temperature=0) via `classify_intent` tool binding
- Pipeline state (`ConversationState`) is passed via `state=` kwarg through `invoke_agent_stream()`

## Testing

- `pytest tests/ -v` — Run all tests
- `pytest tests/test_core.py::TestEventBus -v` — Single test class
- Tests use `create_wot_agent(enable_thinking=False, temperature=0)` to avoid real LLM costs
- Tests autodiscover `src/` via `pyproject.toml` `[tool.pytest.ini_options]`
