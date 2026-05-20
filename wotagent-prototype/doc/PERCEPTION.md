# Perception Agent

## Overview

The Perception Agent is a **rule-based environment monitoring engine** — it continuously evaluates device states against predefined rules and triggers automated actions without LLM involvement.

Unlike the Planner (which uses LLM to parse natural language intent), the Perception Agent uses **zero LLM tokens** — all condition evaluation is pure Python comparison.

## Architecture

```
┌──────────────────────────────────────────────────┐
│              PerceptionEngine                     │
│  ┌──────────────┐    ┌──────────────────┐        │
│  │ Poll cycle   │───▶│ Rule evaluation  │        │
│  │ (drift →     │    │ (condition →     │        │
│  │  poll)       │    │  action)         │        │
│  └──────────────┘    └────────┬─────────┘        │
│                               │                   │
│                        ┌──────▼──────┐            │
│                        │ emit via    │            │
│                        │ EventBus    │            │
│                        └──────┬──────┘            │
└───────────────────────────────┼───────────────────┘
                                │
                ┌───────────────┴──────────────┐
                ▼                               ▼
        ┌────────────────┐            ┌──────────────────┐
        │ execute_plan    │            │ WebSocket (primary)│
        │ directly()     │            │ SSE (legacy)    │
        │ (Executor →    │            │ → Frontend      │
        │  Observer)     │            │                  │
        └────────────────┘            └──────────────────┘
```

### Two Trigger Paths

| Source | Route | LLM cost |
|--------|-------|----------|
| User says "开空调" | Intent Classifier → Executor → Observer | Intent LLM + Executor LLM |
| Rule: temp > 32°C | Executor → Observer directly | Executor LLM only (Planner skipped) |

### Rule → LLM Context Injection

When a user sends a message, the current environment state is injected as `perception_context` into the Planner's system prompt:

```
[Current environment state]
ac-001(currentTemperature=33.5) | light-001(on=False) | humidifier-001(currentHumidity=45.2)
```

This lets the LLM answer "what's the temperature?" without calling a tool — zero extra tokens for the query.

## Module Structure

```
src/wotagent/perception/
├── __init__.py       # Public API exports
├── rules.py          # Condition, RuleAction, EnvironmentRule models + defaults
└── engine.py         # DeviceStateStore, PerceptionEngine, singleton
```

## Core Components

### `rules.py` — Rule Models

```python
@dataclass
class Condition:
    device_id: str
    property_name: str    # e.g. "currentTemperature"
    operator: str          # gt | lt | gte | lte | eq | ne
    value: float | int     # threshold

@dataclass
class RuleAction:
    intent: str            # "control" | "query"
    steps: list[dict]      # Same format as PipelinePlan.steps

@dataclass
class EnvironmentRule:
    name: str
    description: str
    condition: Condition
    action: RuleAction
    cooldown_seconds: int = 300   # Prevent re-trigger spam
    enabled: bool = True
    last_triggered: float = 0.0
```

### Default Rules

| Rule | Condition | Action | Cooldown |
|------|-----------|--------|----------|
| `high_temperature` | ac-001.currentTemperature > 32°C | AC → setMode(cool), setTemperature(26) | 10 min |
| `low_humidity` | humidifier-001.currentHumidity < 30% | Humidifier → turnOn | 10 min |

### `engine.py` — DeviceStateStore

A **SQLite-backed** simulation layer that maintains numeric property values and applies random drift.
Uses WAL journal mode for cross-process consistency:

- **Storage**: SQLite database at `data/state.db` with WAL mode (`PRAGMA journal_mode=WAL`)
- **Concurrency**: Multiple processes (agent + simulator) can read/write simultaneously
- **Reset**: `reset()` clears all rows; `initialize()` re-seeds defaults from Thing Descriptions
- **Temperature**: drifts +0.6/-0.2 per poll (up when AC off, down when AC on)
- **Humidity**: drifts ±2.0 per poll
- **Booleans** (on/off): only change when controlled via `set_property()`

The drift ensures rules eventually trigger, simulating real environmental changes.

**Note**: The simulator project (`wot-device-simulator`) and the agent share the same
`DeviceStateStore` — device state changes via MCP tool calls are visible to the
perception engine immediately, without polling.

### `engine.py` — PerceptionEngine

```python
engine = PerceptionEngine(polling_interval=60)  # poll every 60s
engine.start()        # Background asyncio task
engine.poll_once()    # Manual one-shot poll
engine.stop()         # Cancel background task
engine.get_context()  # Text summary for LLM prompt injection
```

When a rule triggers:
1. `mark_triggered()` sets `last_triggered` for cooldown
2. A `wot.perception.rule_triggered` event is emitted to EventBus
3. The triggered plan is returned for optional execution via `execute_plan_directly()`

### `core/agent.py` — execute_plan_directly()

```python
from wotagent.core import execute_plan_directly

final_text = await execute_plan_directly(
    pipeline, plan_json, plan,
    session_id="sess_xxx",
    user_role="operator",
    memory=memory,
)
```

Skips the Planner entirely and runs Executor → Observer with the pre-structured plan.

## REPL Usage

```
/perception             Show engine status + current environment
/perception on|off      Start / stop the engine
/perception rules       List all rules with status
/perception poll        Trigger one poll cycle now
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/perception/state` | Current environment state + engine stats |
| `GET` | `/api/perception/rules` | All rules with condition/status |
| `POST` | `/api/perception/poll` | Trigger one poll cycle |

## Event Types

| Type | Payload | When |
|------|---------|------|
| `wot.perception.rule_triggered` | `{rule, description, device_id, property, value, threshold, plan}` | A rule condition matched |
| `wot.perception.state` | (reserved for periodic state snapshots) | Future use |

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `ENABLE_PERCEPTION` | `true` | Enable perception engine at startup |
| `PERCEPTION_INTERVAL` | `60` | Polling interval in seconds |
