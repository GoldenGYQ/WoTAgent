# WoTAgent Current Technical Map

Status: current implementation map

Last verified: 2026-07-23

Release baseline: `v2.8.0`; implementation target: `v3.0.0`

This map describes the active runtime after the 2026-07-21 cleanup and the
2026-07-23 risk/scheduling updates. It is a source guide, not a historical
design proposal. See [`README.md`](README.md) for document status and reading
order.

## Runtime hierarchy

```text
Conversation -> Turn -> Run -> Task DAG -> AgentRun -> Loop -> ToolCall
```

```mermaid
flowchart TD
    UI[Vue console] --> API[FastAPI Run API]
    API --> RS[RunService]
    RS --> RC[RunCoordinator]
    RC --> SUP[Butler create_agent Supervisor]
    SUP --> SCH[TaskScheduler]
    SCH --> H[Health create_agent]
    SCH --> C[Care create_agent]
    SCH --> D[Device LangGraph specialists]
    H --> HMCP[Scoped health MCP resources]
    C --> CMCP[Scoped care MCP tools]
    D --> TD[TD + Policy + MCP + verification]
    RC --> STORE[SQLite RunStore]
    STORE --> EVT[Compact RunEvents]
    STORE --> RAW[Raw TraceArtifacts]
```

## Source map

| Boundary | Source | Responsibility |
|---|---|---|
| HTTP assembly | `wotagent-prototype/src/wotagent/api/app.py` | Compose routers and runtime dependencies |
| Run API | `wotagent-prototype/src/wotagent/api/routers/runs.py` | Run, Task, AgentRun, event and TraceArtifact endpoints |
| Application | `wotagent-prototype/src/wotagent/application/run_service.py` | Conversation/Turn/Run lifecycle |
| Run coordination | `wotagent-prototype/src/wotagent/orchestration/run_coordinator.py` | Supervisor loop, Task execution and completion |
| Scheduling | `wotagent-prototype/src/wotagent/orchestration/scheduler.py` | DAG dependencies, conflicts and bounded parallelism |
| Contracts | `wotagent-prototype/src/wotagent/orchestration/run_models.py` | Run, Task, failure, scope, event and artifact models |
| Persistence | `wotagent-prototype/src/wotagent/orchestration/run_store.py` | SQLite Runs, Tasks, AgentRuns, events and raw artifacts |
| Model assembly | `wotagent-prototype/src/wotagent/core/model_runtime.py` | Planner/response model clients only; no orchestration |
| Supervisor | `wotagent-prototype/src/wotagent/agents/supervisor_agent.py` | Structured Task DAG decisions |
| Agent registry | `wotagent-prototype/src/wotagent/agents/specs.py` | Stable Agent IDs, responsibilities and capability scopes |
| Health specialist | `wotagent-prototype/src/wotagent/agents/health.py` | Bounded ReAct over scoped health resources |
| Care specialist | `wotagent-prototype/src/wotagent/agents/care.py` | Bounded ReAct over scoped care tools |
| Device graph | `wotagent-prototype/src/wotagent/agent_graph/graph.py` | Plan, policy, execute, verify, recover and respond |
| Device Planner | `wotagent-prototype/src/wotagent/agent_graph/planner.py` | LLM TaskPlan generation plus TD/schema validation |
| Device boundary | `wotagent-prototype/src/wotagent/gateway/mcp.py` | TD capability discovery and MCP invocation |
| Resource scope | `wotagent-prototype/src/wotagent/domain/resources.py` | Principal/resource/location to deployment device scope |
| Conversation memory | `wotagent-prototype/src/wotagent/conversation_state.py` | Active goal, entities, verified facts and last outcome |
| Domain contract | `wotagent-prototype/src/wotagent/domain/operations.py` | DomainPlugin interface and registry |
| Eldercare plugin | `wotagent-prototype/src/wotagent/domains/eldercare/operations.py` | Eldercare operational projection |
| Smart-home plugin | `wotagent-prototype/src/wotagent/domains/smart_home/operations.py` | Villa/smart-home operational projection |
| Frontend | `frontend/wotagent-v2-console` | Conversation, focused trace and domain console |
| Simulator | `wot-device-simulator` | TD registry, MCP tools and simulated device state |

## Important boundaries

- `smart_home` and `eldercare` are domains. `villa` and the nursing-home demo
  are deployment profiles of those domains.
- Human Principals are not software Agents. Authorization is computed before a
  Task runs and is checked again at MCP/resource boundaries.
- The Supervisor may propose concrete resource claims. Claims can narrow an
  authorized scope but can never add a device to it.
- Hybrid Planner semantics are LLM-driven. The deterministic layer validates
  Agent scope, TD device/action/parameter contracts, Policy and verification.
- A graph reaching its response node is not proof that a delegated Task
  completed. Device action/query Tasks require execution evidence.
- RunEvents remain compact for WebSocket/UI use. Raw prompts, responses and
  tool payloads are stored separately as TraceArtifacts and fetched only on
  demand.
- The same AgentSpec may execute more than one Task in a Run. Each Task gets an
  independent AgentRun; safe read Tasks may run concurrently, while dependency
  and resource-write conflicts are serialized by the Scheduler.
- Risk classification and state transitions are deterministic domain behavior.
  The current eldercare flow supports health/environment readings, risk levels,
  event persistence and `open -> acknowledged -> in_progress -> resolved |
  false_positive`.

## Current verification

Latest full regression recorded on 2026-07-23:

```text
Backend: 262 passed, 3 skipped
Simulator: 64 passed
Frontend: 45 passed, typecheck/build passed
```

Known catalog mismatch: the eldercare Profile configures 16 device IDs, but 15
currently have TD files. `purifier-102` is configured but has no TD. The villa
Profile has TD coverage for all 23 configured devices.

## Removed legacy entry points

- `core/pipeline.py` was replaced by `core/model_runtime.py` because it only
  assembled model clients.
- `orchestration/task_runner.py` and the old top-level `supervisor.py` were
  removed after confirming they had no production callers.
- The production Care Task path no longer uses `EldercareChatAgent`; it now
  uses the same scoped specialist contract as Health.

The active HTTP surface is `/api/v2/*` plus `/api/health`; the active event
socket is `/ws/v2`.
