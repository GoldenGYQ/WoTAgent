# WoTAgent 2.0 Technical Map

This file is a compact source map. The Chinese [complete guide](WOTAGENT_2.0_COMPLETE_TECHNICAL_GUIDE.md) explains the design and interview narrative.

| Area | Source | Responsibility |
|---|---|---|
| API | `wotagent-prototype/src/wotagent/api/app.py` | Task, conversation, confirmation, SSE and WebSocket contracts |
| Model assembly | `core/pipeline.py` | Direct and thinking Planner/Response models |
| Agent loop | `agent_graph/graph.py` | LangGraph nodes, routing and bounded recovery |
| Planning | `agent_graph/planner.py` | Context/capability prompt and typed `TaskPlan` parsing |
| Response | `agent_graph/responder.py` | Evidence-grounded user response |
| Domain | `domain/task.py` | Plan, step, policy, execution and trace schemas |
| Policy | `policy/engine.py` | RBAC, TD action validation and confirmation |
| Execution | `orchestration/engine.py` | Deterministic tool invocation and state verification |
| Device boundary | `gateway/mcp.py` | TD capability discovery and MCP calls |
| SimuHome | `gateway/simuhome.py` | Matter/SimuHome state to TD adaptation |
| Persistence | `conversation.py` | SQLite conversations, turns and pending goals |
| Observability | `telemetry/store.py` | Ordered events, usage, TTFT, cost and loop metrics |
| Evaluation | `evaluation/` and `scripts/evaluate_*_v2.py` | HomeBench/SimuHome adapters and reports |
| Frontend | `frontend/wotagent-v2-console` | Conversation and trace monitoring UI |
| Simulator | `wot-device-simulator` | Device state, TD registry and generated MCP tools |

The active HTTP surface is `/api/v2/*` plus `/api/health`; the active trace socket is `/ws/v2`.
