"""Simulator REST API + MCP over HTTP — single FastMCP server, no FastAPI wrapper.

All endpoints run on the same FastMCP HTTP server via ``custom_route()``.
Agent connects to ``/mcp`` (Streamable HTTP).  Dashboard uses REST endpoints.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from .simulator import DeviceStateStore, get_simulator
from .td import find_devices, get_device_by_id, load_tds

# ── Simulator engine ─────────────────────────────────────────────────────
_sim = get_simulator(polling_interval=30, auto_start=True)

# ── FastMCP server (MCP tools + REST endpoints in one process) ──────────
from .mcp_server import _build_server

mcp = _build_server(sim=_sim)


# ── Dashboard ────────────────────────────────────────────────────────────

def _dashboard_path() -> Path:
    return Path(__file__).resolve().parent / "dashboard.html"


def _load_dashboard() -> str:
    fp = _dashboard_path()
    if fp.exists():
        return fp.read_text(encoding="utf-8")
    return "<h1>Dashboard not found</h1>"


# ── Weather helpers ──────────────────────────────────────────────────────

def _normalize_weather(payload: dict[str, Any], city: str) -> dict[str, Any]:
    cond = (payload.get("current_condition") or [{}])[0]
    temp_c = cond.get("temp_C")
    humidity = cond.get("humidity")
    wind_kph = cond.get("windspeedKmph")
    desc_arr = cond.get("weatherDesc") or []
    desc = desc_arr[0].get("value", "") if desc_arr else ""
    return {
        "city": city,
        "temperature": float(temp_c) if temp_c is not None else None,
        "humidity": float(humidity) if humidity is not None else None,
        "wind_kph": float(wind_kph) if wind_kph is not None else None,
        "description": desc,
        "raw": payload,
    }


def _fetch_weather_http(city: str) -> dict[str, Any]:
    url = f"https://wttr.in/{city}?format=j1"
    with urlopen(url, timeout=8.0) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return _normalize_weather(payload, city)


def _fetch_weather_shell(city: str) -> dict[str, Any]:
    url = f"https://wttr.in/{city}?format=j1"
    proc = subprocess.run(
        ["curl", "-s", url],
        check=True,
        capture_output=True,
        text=True,
        timeout=12,
    )
    payload = json.loads(proc.stdout)
    return _normalize_weather(payload, city)


def _parse_value(v: Any) -> Any:
    if isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        t = v.strip()
        if t.lower() in ("true", "false"):
            return t.lower() == "true"
        try:
            if "." in t:
                return float(t)
            return int(t)
        except ValueError:
            return v
    return v


# ── REST endpoints via custom_route ──────────────────────────────────────


@mcp.custom_route("/", methods=["GET"])
async def dashboard_root(request: Request) -> HTMLResponse:
    return HTMLResponse(_load_dashboard())


@mcp.custom_route("/dashboard", methods=["GET"])
async def dashboard_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_load_dashboard())


@mcp.custom_route("/api/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "engine_running": _sim.is_running})


@mcp.custom_route("/api/devices", methods=["GET"])
async def list_devices(request: Request) -> JSONResponse:
    location = request.query_params.get("location")
    devices = find_devices(location=location) if location else find_devices()
    return JSONResponse({
        "devices": [
            {
                "id": d.device_id,
                "device_id": d.device_id,
                "title": d.title,
                "location": d.location,
                "capabilities": d.capabilities,
                "actions": [a.name for a in d.actions],
                "properties": [p.name for p in d.properties],
            }
            for d in devices
        ]
    })


@mcp.custom_route("/api/state", methods=["GET"])
async def state(request: Request) -> JSONResponse:
    DeviceStateStore.initialize()
    return JSONResponse({
        "summary": _sim.get_context(),
        "stats": _sim.stats,
        "devices": DeviceStateStore.get_all(),
    })


@mcp.custom_route("/api/state/{device_id}", methods=["GET"])
async def state_by_device(request: Request) -> JSONResponse:
    device_id = request.path_params["device_id"]
    DeviceStateStore.initialize()
    return JSONResponse({
        "device_id": device_id,
        "state": DeviceStateStore.get(device_id),
    })


@mcp.custom_route("/api/poll", methods=["POST"])
async def poll_once(request: Request) -> JSONResponse:
    await _sim.poll_once()
    return JSONResponse({"success": True, "message": "Drift cycle completed"})


@mcp.custom_route("/api/events", methods=["GET"])
async def list_events(request: Request) -> JSONResponse:
    limit = int(request.query_params.get("limit", 50))
    return JSONResponse({"events": _sim.recent_events(limit=limit)})


@mcp.custom_route("/api/events/inject", methods=["POST"])
async def inject_event(request: Request) -> JSONResponse:
    body = await request.json()
    event_type = str(body.get("type", "wot.sim.manual_event"))
    data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
    result = await _sim.inject_event(event_type=event_type, data=data)
    return JSONResponse(result)


@mcp.custom_route("/api/state/patch", methods=["POST"])
async def patch_state(request: Request) -> JSONResponse:
    body = await request.json()
    device_id = str(body.get("device_id", ""))
    prop = str(body.get("property", ""))
    if not device_id or not prop:
        return JSONResponse({"success": False, "error": "device_id and property are required"})
    val = _parse_value(body.get("value"))
    result = await _sim.patch_state(device_id=device_id, property_name=prop, value=val)
    return JSONResponse(result)


@mcp.custom_route("/api/environment/set", methods=["POST"])
async def set_environment(request: Request) -> JSONResponse:
    body = await request.json()
    values: dict[str, Any] = {}
    for key in ("temperature", "humidity", "pm25", "gasLevel"):
        if key in body and body[key] is not None:
            values[key] = _parse_value(body[key])
    location = body.get("location")
    result = await _sim.apply_environment(values=values, location=str(location) if location else None)
    return JSONResponse(result)


@mcp.custom_route("/api/weather/fetch", methods=["GET"])
async def weather_fetch(request: Request) -> JSONResponse:
    city = request.query_params.get("city", "beijing")
    mode = request.query_params.get("mode", "shell")
    use_shell = mode.strip().lower() == "shell"
    try:
        weather = _fetch_weather_shell(city) if use_shell else _fetch_weather_http(city)
        return JSONResponse({"success": True, "source": "shell" if use_shell else "http", "weather": weather})
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)})


@mcp.custom_route("/api/weather/apply", methods=["POST"])
async def weather_apply(request: Request) -> JSONResponse:
    body = await request.json()
    city = str(body.get("city", "beijing"))
    mode = str(body.get("mode", "shell"))
    location = body.get("location")

    try:
        weather = _fetch_weather_shell(city) if mode == "shell" else _fetch_weather_http(city)
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)})

    values = {
        "temperature": weather.get("temperature"),
        "humidity": weather.get("humidity"),
    }
    result = await _sim.apply_environment(values=values, location=str(location) if location else None)
    return JSONResponse({
        "success": True,
        "weather": weather,
        "applied": result,
    })


# ── Legacy alias: POST /api/control (kept for dashboard button) ─────────

@mcp.custom_route("/api/control", methods=["POST"])
async def control(request: Request) -> JSONResponse:
    body = await request.json()
    result = await _sim.control_device(
        device_id=str(body.get("device_id", "")),
        action=str(body.get("action", "")),
        parameters=body.get("parameters", {}),
    )
    return JSONResponse(result)


# ── Entry point ──────────────────────────────────────────────────────────

def main() -> None:
    mcp.run(transport="http", host="127.0.0.1", port=18080)
