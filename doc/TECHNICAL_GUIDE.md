# WoTAgent Technical Guide

## Scope

This document is the cross-project technical map for:
- `wotagent-prototype` (agent backend)
- `wot-device-simulator` (simulation platform)

It focuses on file-level responsibilities so collaborators can quickly locate logic.

## Agent Backend File Map (`wotagent-prototype/src/wotagent`)

### Root package

| File | Responsibility |
|------|----------------|
| `__init__.py` | Package exports and top-level architecture summary. |
| `cli.py` | CLI entry (`wotagent repl`, `wotagent api`, `wotagent log`). |
| `repl_impl.py` | Interactive REPL implementation and slash-command handling. |
| `log_viewer.py` | Streaming/pretty printing for local log tailing. |
| `schemas.py` | Shared pydantic/dataclass-style API payload models. |

### API layer

| File | Responsibility |
|------|----------------|
| `api/__init__.py` | API package exports. |
| `api/app.py` | FastAPI app, WebSocket `/ws`, REST endpoints, session/perception endpoints, app bootstrap. |
| `api/static/dashboard.html` | Lightweight static dashboard for backend-side inspection and event viewing. |

### Core pipeline

| File | Responsibility |
|------|----------------|
| `core/__init__.py` | Public core exports (`create_wot_pipeline`, invoke helpers). |
| `core/agent.py` | Intent-classification-first runtime pipeline, streaming, event emission, transcript integration. |
| `core/session.py` | Session lifecycle and session manager. |
| `core/state.py` | `ConversationState` for intent continuity across turns. |

### Events and logging

| File | Responsibility |
|------|----------------|
| `events/__init__.py` | Event package exports. |
| `events/bus.py` | In-memory pub/sub event bus with history cursor support. |
| `events/schema.py` | CloudEvents schema and allowed event type definitions. |
| `logging/__init__.py` | Logging package exports. |
| `logging/logger.py` | Logger setup and EventBus logger bridge. |

### Memory and prompts

| File | Responsibility |
|------|----------------|
| `memory/__init__.py` | Memory package exports. |
| `memory/manager.py` | Conversation memory persistence and `SessionTranscript` JSONL recording. |
| `prompts/__init__.py` | Prompt package exports. |
| `prompts/templates.py` | System prompt templates for executor/observer/responder and compatibility planner template. |

### Tools and integrations

| File | Responsibility |
|------|----------------|
| `tools/__init__.py` | Tool package exports and registry composition. |
| `tools/iot.py` | IoT-facing tools (`list`, `query`, `control`) and related EventBus emissions. |
| `tools/mcp_client.py` | MCP client wrapper/transport helpers. |
| `tools/registry.py` | Non-IoT utility tools and tool registration. |
| `rag/__init__.py` | RAG package exports. |
| `rag/retriever.py` | Chroma-based semantic retrieval over TD/domain docs. |

### Domain modules

| File | Responsibility |
|------|----------------|
| `wot/__init__.py` | WoT domain exports. |
| `wot/td.py` | Thing Description loading, lookup, and device discovery helpers. |
| `perception/__init__.py` | Perception module exports. |
| `perception/rules.py` | Rule model definitions and default rules. |
| `perception/engine.py` | Polling engine, rule evaluation, and perception-trigger execution glue. |
| `auth/__init__.py` | Auth package exports. |
| `auth/rbac.py` | RBAC role and permission checks (`viewer` / `operator` / `admin`). |

## Simulator File Map (`wot-device-simulator/src/wot_device_simulator`)

| File | Responsibility |
|------|----------------|
| `__init__.py` | Simulator package exports and version metadata. |
| `service.py` | FastAPI simulator service (REST + WebSocket `/ws/events`) and dashboard serving. |
| `mcp_server.py` | MCP server implementation exposing simulator operations as MCP tools. |
| `simulator.py` | Core simulator runtime, state store, polling/drift loop, event stream backend. |
| `rules.py` | Simulator-side automation/rule utilities. |
| `td.py` | TD loading and simulator device lookup helpers. |
| `state_cli.py` | CLI helpers for direct state inspection/patching from terminal. |
| `dashboard.html` | Standalone simulator dashboard page. |

## Startup Script Map

| File | Responsibility |
|------|----------------|
| `scripts/start-simulator.bat` | One-click startup for simulator API (`127.0.0.1:18080`). |
| `scripts/start-agent.bat` | One-click startup for WoT agent backend (`127.0.0.1:8000`). |
| `scripts/start-frontend.bat` | One-click startup for Vue frontend (`localhost:5173`). |

## Documentation Placement Rationale

- Root-level `README.md`: onboarding and execution order (what to run first).
- Project-internal docs under `wotagent-prototype/doc`: architecture internals tied to backend implementation.
- Cross-project operational docs under `doc/`: repository-wide technical map and API guide.
