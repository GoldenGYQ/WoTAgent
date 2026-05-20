# wot-device-simulator

A standalone IoT device simulator for agent development.

## Features

- TD loading and discovery
- In-memory dynamic state simulation
- Drift models for temperature/humidity/PM2.5/gas
- Rule-triggered automation plans
- REST + WebSocket subscription service
- MCP tools for agent-side integration

## Run API service

```bash
wot-sim-api
```

If the command is not available yet, run:

```bash
uv sync
```

Then use:

```bash
uv run wot-sim-api
```

Default URL: `http://127.0.0.1:18080`

Control panel: `http://127.0.0.1:18080/dashboard`

## Control APIs

- `GET /api/state` current simulator state
- `POST /api/control` control a device action
- `POST /api/state/patch` manually patch one device property
- `POST /api/environment/set` set temperature/humidity/pm25/gasLevel in batch
- `POST /api/events/inject` inject a custom event
- `GET /api/events?limit=50` read recent event history
- `GET /api/weather/fetch?city=beijing&mode=shell` fetch weather (`shell` or `http`)
- `POST /api/weather/apply` fetch weather and apply to environment
- `WS /ws/events` subscribe simulator events

## Run MCP server

```bash
wot-sim-mcp
```

If needed, use:

```bash
uv run wot-sim-mcp
```

MCP tools include device listing, TD download, control, state patching,
environment setting, event injection, and one-shot polling.

## TD source

By default TD files are loaded from `data/td/*.json`.

Optional env vars:

- `WOT_SIM_TD_ROOT`: local TD directory
- `WOT_SIM_TD_REGISTRY`: remote TD registry base URL, e.g. `http://127.0.0.1:18080`
