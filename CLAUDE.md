# WoTAgent 2.0 Repository Notes

## Architecture

The repository has three active projects:

- `wotagent-prototype`: FastAPI + LangGraph backend and evaluation suite.
- `frontend/wotagent-v2-console`: Vue 3 task and trace console.
- `wot-device-simulator`: FastMCP device simulator, TD registry, REST API, and dashboard.

Runtime flow:

```text
message -> typed Planner -> PolicyEngine -> deterministic TaskOrchestrator
-> MCPDeviceGateway -> TD-generated MCP tool -> device
-> postcondition verification / bounded recovery -> Response Agent
```

The LLM never owns authorization or direct hardware execution. Conversation turns and pending goals are persisted in SQLite. LangGraph checkpoints support confirmation resume. `InMemoryTraceStore` provides ordered event replay, usage, TTFT, cost and loop metrics.

## Commands

```powershell
cd wotagent-prototype
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m wotagent.cli api --port 8000

cd ..\frontend\wotagent-v2-console
npm test
npm run build
npm run dev

cd ..\..\wot-device-simulator
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m wot_device_simulator.service
```

## Boundaries

- Do not reintroduce the v1 EventBus, ReAct Executor, polling perception engine, JSONL session memory, or old `/api/chat` and `/ws` contracts.
- Device affordances must come from TD and be invoked through MCP.
- Policy and postcondition checks remain outside the model.
- Preserve trace events when adding graph nodes or recovery behavior.
- `frontend/wotagent-v2-console` is the only Agent frontend; the simulator dashboard remains a separate device-state view.
