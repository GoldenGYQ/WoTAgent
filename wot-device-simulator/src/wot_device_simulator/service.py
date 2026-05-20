from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from .simulator import DeviceStateStore, get_simulator
from .td import find_devices, get_device_by_id, load_tds

app = FastAPI(
    title="wot-device-simulator",
    description="Standalone IoT simulation service",
    version="0.1.0",
)

_sim = get_simulator(polling_interval=30, auto_start=True)


def _dashboard_path() -> Path:
    return Path(__file__).resolve().parent / "dashboard.html"


def _load_dashboard() -> str:
    fp = _dashboard_path()
    if fp.exists():
        return fp.read_text(encoding="utf-8")
    return "<h1>Dashboard not found</h1>"


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


def _parse_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("1", "true", "yes", "on"):
            return True
        if t in ("0", "false", "no", "off"):
            return False
    return default


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


@app.get("/", response_class=HTMLResponse)
async def dashboard_root() -> str:
    return _load_dashboard()


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page() -> str:
    return _load_dashboard()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "engine_running": _sim.is_running}


@app.get("/api/td")
async def list_tds() -> dict[str, Any]:
    return {"tds": [td.raw for td in load_tds()]}


@app.get("/api/td/{device_id}")
async def get_td(device_id: str) -> dict[str, Any]:
    td = get_device_by_id(device_id)
    if td is None:
        return {"success": False, "error": f"TD '{device_id}' not found"}
    return {"success": True, "td": td.raw}


@app.get("/api/devices")
async def list_devices(location: str | None = None) -> dict[str, Any]:
    devices = find_devices(location=location) if location else find_devices()
    return {
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
    }


@app.get("/api/state")
async def state() -> dict[str, Any]:
    DeviceStateStore.initialize()
    return {
        "summary": _sim.get_context(),
        "stats": _sim.stats,
        "devices": DeviceStateStore.get_all(),
    }


@app.get("/api/state/{device_id}")
async def state_by_device(device_id: str) -> dict[str, Any]:
    DeviceStateStore.initialize()
    return {
        "device_id": device_id,
        "state": DeviceStateStore.get(device_id),
    }


@app.post("/api/control")
async def control(payload: dict[str, Any]) -> dict[str, Any]:
    return await _sim.control_device(
        device_id=str(payload.get("device_id", "")),
        action=str(payload.get("action", "")),
        parameters=payload.get("parameters", {}),
    )


@app.post("/api/poll")
async def poll_once() -> dict[str, Any]:
    rules = await _sim.poll_once()
    return {"triggered": len(rules), "rules": rules}


@app.get("/api/events")
async def list_events(limit: int = 50) -> dict[str, Any]:
    return {"events": _sim.recent_events(limit=limit)}


@app.post("/api/events/inject")
async def inject_event(payload: dict[str, Any]) -> dict[str, Any]:
    event_type = str(payload.get("type", "wot.sim.manual_event"))
    data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
    return await _sim.inject_event(event_type=event_type, data=data)


@app.post("/api/state/patch")
async def patch_state(payload: dict[str, Any]) -> dict[str, Any]:
    device_id = str(payload.get("device_id", ""))
    prop = str(payload.get("property", ""))
    if not device_id or not prop:
        return {"success": False, "error": "device_id and property are required"}
    val = _parse_value(payload.get("value"))
    return await _sim.patch_state(device_id=device_id, property_name=prop, value=val)


@app.post("/api/environment/set")
async def set_environment(payload: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key in ("temperature", "humidity", "pm25", "gasLevel"):
        if key in payload and payload[key] is not None:
            values[key] = _parse_value(payload[key])
    location = payload.get("location")
    return await _sim.apply_environment(values=values, location=str(location) if location else None)


@app.get("/api/weather/fetch")
async def weather_fetch(city: str = "beijing", mode: str = "shell") -> dict[str, Any]:
    use_shell = str(mode).strip().lower() == "shell"
    try:
        weather = _fetch_weather_shell(city) if use_shell else _fetch_weather_http(city)
        return {"success": True, "source": "shell" if use_shell else "http", "weather": weather}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.post("/api/weather/apply")
async def weather_apply(payload: dict[str, Any]) -> dict[str, Any]:
    city = str(payload.get("city", "beijing"))
    mode = str(payload.get("mode", "shell"))
    location = payload.get("location")

    try:
        weather = _fetch_weather_shell(city) if mode == "shell" else _fetch_weather_http(city)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    values = {
        "temperature": weather.get("temperature"),
        "humidity": weather.get("humidity"),
    }
    result = await _sim.apply_environment(values=values, location=str(location) if location else None)
    return {
        "success": True,
        "weather": weather,
        "applied": result,
    }


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    await websocket.accept()
    q = _sim.subscribe()
    try:
        while True:
            evt = await q.get()
            await websocket.send_json(evt)
    except WebSocketDisconnect:
        pass
    finally:
        _sim.unsubscribe(q)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "wot_device_simulator.service:app",
        host="127.0.0.1",
        port=18080,
        reload=False,
    )
