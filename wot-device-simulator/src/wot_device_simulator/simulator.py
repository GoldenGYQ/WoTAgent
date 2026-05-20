from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
import time
from pathlib import Path
from typing import Any

from .rules import EnvironmentRule, default_rules
from .td import find_devices, get_device_by_id, load_tds

logger = logging.getLogger(__name__)


_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "state.db"
_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.row_factory = sqlite3.Row
    return _conn


def _migrate_from_json() -> None:
    json_fp = _DB_PATH.with_name("state.json")
    if not json_fp.exists():
        return
    conn = _get_db()
    count = conn.execute("SELECT COUNT(*) FROM device_state").fetchone()[0]
    if count > 0:
        return
    try:
        raw = json.loads(json_fp.read_text(encoding="utf-8"))
        devices = raw.get("devices", {}) if isinstance(raw, dict) else raw
        if not isinstance(devices, dict):
            return
        now = time.time()
        migrated = 0
        for dev_id, state in devices.items():
            if isinstance(state, dict):
                conn.execute(
                    "INSERT OR IGNORE INTO device_state VALUES (?, ?, ?)",
                    (dev_id, json.dumps(state), now),
                )
                migrated += 1
        conn.commit()
        if migrated:
            logger.info("Migrated %d devices from state.json", migrated)
    except (OSError, json.JSONDecodeError):
        pass


def _default_property_value(prop_name: str, prop_type: str) -> Any:
    if prop_type == "boolean":
        return False
    if prop_type in ("number", "integer"):
        name_lower = prop_name.lower()
        if "temp" in name_lower:
            return round(random.uniform(24.0, 34.0), 1)
        if "humid" in name_lower:
            return round(random.uniform(25.0, 65.0), 1)
        if "bright" in name_lower or "level" in name_lower:
            return 0
        return 0
    return ""


class DeviceStateStore:
    _initialized: bool = False

    @classmethod
    def initialize(cls) -> None:
        if cls._initialized:
            return
        conn = _get_db()
        conn.execute("""CREATE TABLE IF NOT EXISTS device_state (
            device_id TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            updated_at REAL NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS device_info (
            device_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            capabilities TEXT NOT NULL DEFAULT '[]'
        )""")
        conn.commit()
        _migrate_from_json()
        now = time.time()
        for td in load_tds():
            row = conn.execute(
                "SELECT state FROM device_state WHERE device_id = ?", (td.device_id,)
            ).fetchone()
            props = json.loads(row[0]) if row else {}
            changed = False
            for p in td.properties:
                if p.name not in props:
                    props[p.name] = _default_property_value(p.name, p.type)
                    changed = True
            if changed or row is None:
                conn.execute(
                    "INSERT OR REPLACE INTO device_state VALUES (?, ?, ?)",
                    (td.device_id, json.dumps(props), now),
                )
            conn.execute(
                "INSERT OR IGNORE INTO device_info VALUES (?, ?, ?, ?)",
                (td.device_id, td.title, td.location, json.dumps(td.capabilities)),
            )
        conn.commit()
        cls._initialized = True
        logger.info("DeviceStateStore initialized")

    @classmethod
    def reset(cls) -> None:
        cls._initialized = False
        conn = _get_db()
        conn.execute("DELETE FROM device_state")
        conn.execute("DELETE FROM device_info")
        conn.commit()

    @classmethod
    def save(cls) -> None:
        pass  # SQLite commits on every write

    @classmethod
    def get_all(cls) -> dict[str, dict[str, Any]]:
        cls.initialize()
        conn = _get_db()
        rows = conn.execute("SELECT device_id, state FROM device_state").fetchall()
        return {row["device_id"]: json.loads(row["state"]) for row in rows}

    @classmethod
    def get(cls, device_id: str) -> dict[str, Any]:
        cls.initialize()
        conn = _get_db()
        row = conn.execute(
            "SELECT state FROM device_state WHERE device_id = ?", (device_id,)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    @classmethod
    def get_property(cls, device_id: str, prop_name: str) -> Any | None:
        return cls.get(device_id).get(prop_name)

    @classmethod
    def set_property(cls, device_id: str, prop_name: str, value: Any) -> None:
        conn = _get_db()
        state = cls.get(device_id)
        state[prop_name] = value
        conn.execute(
            "INSERT OR REPLACE INTO device_state VALUES (?, ?, ?)",
            (device_id, json.dumps(state), time.time()),
        )
        conn.commit()

    @classmethod
    def drift(cls) -> None:
        cls.initialize()
        conn = _get_db()
        rows = conn.execute("SELECT device_id, state FROM device_state").fetchall()
        all_states: dict[str, dict[str, Any]] = {}
        for row in rows:
            all_states[row["device_id"]] = json.loads(row["state"])

        for dev_id, props in all_states.items():
            temp_keys = [k for k in props if "temp" in k.lower() and k != "targetTemp"]
            is_ac_on = props.get("on", False) is True
            mode = str(props.get("mode", "")).lower()
            is_cooling = is_ac_on and mode in ("cool", "auto", "")
            target = props.get("targetTemp", 24)

            for k in temp_keys:
                val = props[k]
                if isinstance(val, (int, float)):
                    if is_cooling:
                        if val > target:
                            props[k] = round(max(val - 0.5, target), 1)
                        elif val < target - 1:
                            props[k] = round(min(val + 0.3, target), 1)
                        else:
                            props[k] = round(val + random.uniform(-0.2, 0.2), 1)
                    else:
                        props[k] = round(val + random.uniform(0.1, 0.4), 1)

            humid_keys = [k for k in props if "humid" in k.lower()]
            is_on_h = props.get("on", False) is True
            for k in humid_keys:
                val = props[k]
                if isinstance(val, (int, float)):
                    if is_on_h:
                        props[k] = round(min(val + random.uniform(1.0, 3.0), 65), 1)
                    else:
                        props[k] = round(max(val - random.uniform(0.5, 1.5), 15), 1)

            pm_keys = [k for k in props if k == "pm25"]
            is_on_p = props.get("on", False) is True
            for k in pm_keys:
                val = props[k]
                if isinstance(val, (int, float)):
                    if is_on_p:
                        props[k] = round(max(val - random.uniform(3, 8), 5), 1)
                    else:
                        props[k] = round(min(val + random.uniform(0, 3), 150), 1)

            if "gasLevel" in props:
                gl = props["gasLevel"]
                if isinstance(gl, (int, float)):
                    is_exhaust = False
                    for other_id, other_p in all_states.items():
                        if other_id != dev_id and other_p.get("on") is True:
                            is_exhaust = True
                            break
                    if is_exhaust:
                        props["gasLevel"] = round(max(gl - random.uniform(2, 5), 0), 1)
                    else:
                        props["gasLevel"] = round(min(gl + random.uniform(0, 1), 100), 1)

            if "alarm" in props and isinstance(props.get("gasLevel"), (int, float)):
                if props["gasLevel"] < 5:
                    props["alarm"] = False

        now = time.time()
        for dev_id, props in all_states.items():
            conn.execute(
                "INSERT OR REPLACE INTO device_state VALUES (?, ?, ?)",
                (dev_id, json.dumps(props), now),
            )
        conn.commit()

    @classmethod
    def format_summary(cls) -> str:
        cls.initialize()
        conn = _get_db()
        rows = conn.execute("SELECT device_id, state FROM device_state").fetchall()
        parts: list[str] = []
        for row in rows:
            dev_id, state_json = row["device_id"], row["state"]
            props = json.loads(state_json)
            vals = ", ".join(f"{k}={v}" for k, v in props.items())
            parts.append(f"{dev_id}({vals})")
        return " | ".join(parts)

    @classmethod
    def get_all_with_info(cls) -> list[dict[str, Any]]:
        """Return all devices with location + state combined, one dict per device."""
        cls.initialize()
        conn = _get_db()
        rows = conn.execute("""
            SELECT d.device_id, d.title, d.location, d.capabilities, s.state
            FROM device_info d
            LEFT JOIN device_state s ON d.device_id = s.device_id
            ORDER BY d.device_id
        """).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append({
                "device_id": row["device_id"],
                "title": row["title"],
                "location": row["location"],
                "capabilities": json.loads(row["capabilities"]),
                "state": json.loads(row["state"]) if row["state"] else {},
            })
        return result


class SimulatorEngine:
    def __init__(
        self,
        rules: list[EnvironmentRule] | None = None,
        polling_interval: int = 60,
    ) -> None:
        self.rules = rules or default_rules()
        self.polling_interval = polling_interval
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._stats: dict[str, int] = {"polls": 0, "triggers": 0, "controls": 0}
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._event_history: list[dict[str, Any]] = []

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        DeviceStateStore.initialize()
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        self._task = loop.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    async def _publish(self, event: dict[str, Any]) -> None:
        if "time" not in event:
            event["time"] = time.time()
        self._event_history.append(event)
        if len(self._event_history) > 500:
            self._event_history = self._event_history[-500:]

        stale: list[asyncio.Queue[dict[str, Any]]] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                stale.append(q)
        for q in stale:
            self._subscribers.discard(q)

    async def _run_loop(self) -> None:
        try:
            while self._running:
                DeviceStateStore.drift()
                await self._evaluate_rules()
                self._stats["polls"] += 1
                await asyncio.sleep(self.polling_interval)
        except asyncio.CancelledError:
            pass

    async def poll_once(self) -> list[dict[str, Any]]:
        DeviceStateStore.drift()
        self._stats["polls"] += 1
        return await self._evaluate_rules()

    async def _evaluate_rules(self) -> list[dict[str, Any]]:
        triggered: list[dict[str, Any]] = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            prop_val = DeviceStateStore.get_property(rule.condition.device_id, rule.condition.property_name)
            if prop_val is None:
                continue
            if rule.condition.evaluate(prop_val) and rule.is_ready():
                rule.mark_triggered()
                plan = {
                    "intent": rule.action.intent,
                    "rationale": rule.description,
                    "steps": rule.action.steps,
                    "triggered_by": rule.name,
                }
                triggered.append(plan)
                self._stats["triggers"] += 1
                await self._publish(
                    {
                        "type": "wot.sim.rule_triggered",
                        "data": {
                            "rule": rule.name,
                            "description": rule.description,
                            "device_id": rule.condition.device_id,
                            "property": rule.condition.property_name,
                            "value": prop_val,
                            "threshold": rule.condition.value,
                            "plan": plan,
                        },
                    }
                )
        return triggered

    async def control_device(
        self,
        device_id: str,
        action: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        device = get_device_by_id(device_id)
        if device is None:
            candidates = find_devices()
            for d in candidates:
                if device_id in d.device_id or device_id in d.title:
                    device = d
                    break

        if device is None and not device_id:
            action_lower = action.lower()
            if any(token in action_lower for token in ("turnon", "turn_off", "turnoff", "brightness", "bright")):
                lights = find_devices(capability="luminance", location="living_room", semantic=True) or find_devices(capability="luminance", semantic=True)
                if lights:
                    device = lights[0]
            elif any(token in action_lower for token in ("temp", "temperature", "mode")):
                devices = find_devices(capability="temperature", semantic=True)
                if devices:
                    device = devices[0]

        if device is None:
            return {"success": False, "error": f"Device '{device_id}' not found"}

        actual_device_id = device.device_id
        params = parameters or {}
        DeviceStateStore.initialize()

        action_lower = action.lower()
        if action_lower in ("turnon", "turn_on"):
            DeviceStateStore.set_property(actual_device_id, "on", True)
            if "brightness" in DeviceStateStore.get(actual_device_id):
                DeviceStateStore.set_property(actual_device_id, "brightness", 80)
        elif action_lower in ("turnoff", "turn_off"):
            DeviceStateStore.set_property(actual_device_id, "on", False)
            if "brightness" in DeviceStateStore.get(actual_device_id):
                DeviceStateStore.set_property(actual_device_id, "brightness", 0)
        elif "brightness" in action_lower or "bright" in action_lower:
            level = params.get("level", params.get("brightness", 50))
            DeviceStateStore.set_property(actual_device_id, "on", bool(level > 0))
            DeviceStateStore.set_property(actual_device_id, "brightness", level)
        elif "temp" in action_lower:
            temp = params.get("temp", params.get("temperature", params.get("value", 24)))
            DeviceStateStore.set_property(actual_device_id, "targetTemp", temp)
            DeviceStateStore.set_property(actual_device_id, "on", True)
            # Pull current temperature toward target immediately
            cur = DeviceStateStore.get(actual_device_id)
            for k in cur:
                if "temp" in k.lower() and k != "targetTemp":
                    cv = cur[k]
                    if isinstance(cv, (int, float)):
                        if cv > temp:
                            DeviceStateStore.set_property(actual_device_id, k, round(max(cv - 2, temp), 1))
                        elif cv < temp:
                            DeviceStateStore.set_property(actual_device_id, k, round(min(cv + 2, temp), 1))
        elif "mode" in action_lower:
            mode = params.get("mode", "cool")
            DeviceStateStore.set_property(actual_device_id, "mode", mode)
        elif "speed" in action_lower:
            speed = params.get("speed", params.get("level", 1))
            DeviceStateStore.set_property(actual_device_id, "on", bool(speed > 0))
            DeviceStateStore.set_property(actual_device_id, "speed", speed)
        elif "volume" in action_lower:
            vol = params.get("volume", params.get("level", 10))
            DeviceStateStore.set_property(actual_device_id, "volume", vol)
        elif "mist" in action_lower:
            level = params.get("level", 1)
            DeviceStateStore.set_property(actual_device_id, "on", bool(level > 0))
            DeviceStateStore.set_property(actual_device_id, "mistLevel", level)

        self._stats["controls"] += 1
        new_state = DeviceStateStore.get(actual_device_id)
        await self._publish(
            {
                "type": "wot.sim.device_controlled",
                "data": {
                    "device": device.title,
                    "device_id": actual_device_id,
                    "action": action,
                    "parameters": params,
                    "new_state": new_state,
                },
            }
        )

        return {
            "success": True,
            "device": device.title,
            "device_id": actual_device_id,
            "action": action,
            "parameters": params,
            "new_state": new_state,
            "message": f"Executed {action} on {device.title}",
        }

    async def patch_state(self, device_id: str, property_name: str, value: Any) -> dict[str, Any]:
        DeviceStateStore.initialize()
        old_state = DeviceStateStore.get(device_id)
        if not old_state:
            return {"success": False, "error": f"Device '{device_id}' not found"}

        old_value = old_state.get(property_name)
        DeviceStateStore.set_property(device_id, property_name, value)
        new_value = DeviceStateStore.get(device_id).get(property_name)

        await self._publish(
            {
                "type": "wot.sim.state_patched",
                "data": {
                    "device_id": device_id,
                    "property": property_name,
                    "old_value": old_value,
                    "new_value": new_value,
                },
            }
        )

        return {
            "success": True,
            "device_id": device_id,
            "property": property_name,
            "old_value": old_value,
            "new_value": new_value,
        }

    async def apply_environment(self, values: dict[str, Any], location: str | None = None) -> dict[str, Any]:
        DeviceStateStore.initialize()
        all_states = DeviceStateStore.get_all()
        changed: list[dict[str, Any]] = []

        temp = values.get("temperature")
        humid = values.get("humidity")
        pm25 = values.get("pm25")
        gas = values.get("gasLevel")

        devices = find_devices(location=location) if location else find_devices()
        allowed_ids = {d.device_id for d in devices}

        for dev_id, props in all_states.items():
            if location and dev_id not in allowed_ids:
                continue

            for prop_name in list(props.keys()):
                lowered = prop_name.lower()
                target_val: Any | None = None
                if temp is not None and "temp" in lowered and prop_name != "targetTemp":
                    target_val = temp
                elif humid is not None and "humid" in lowered:
                    target_val = humid
                elif pm25 is not None and lowered == "pm25":
                    target_val = pm25
                elif gas is not None and prop_name == "gasLevel":
                    target_val = gas

                if target_val is not None:
                    old_val = DeviceStateStore.get(dev_id).get(prop_name)
                    DeviceStateStore.set_property(dev_id, prop_name, target_val)
                    changed.append(
                        {
                            "device_id": dev_id,
                            "property": prop_name,
                            "old_value": old_val,
                            "new_value": target_val,
                        }
                    )

        await self._publish(
            {
                "type": "wot.sim.environment_applied",
                "data": {
                    "location": location,
                    "values": values,
                    "changed": changed,
                },
            }
        )
        return {"success": True, "changed": changed, "count": len(changed)}

    async def inject_event(self, event_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {
            "type": event_type,
            "data": data or {},
        }
        await self._publish(event)
        return {"success": True, "event": event}

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        return list(self._event_history[-limit:])

    def get_context(self, max_length: int = 500) -> str:
        summary = DeviceStateStore.format_summary()
        if len(summary) > max_length:
            summary = summary[:max_length] + "..."
        if not summary:
            return ""
        return f"[Current environment state]\\n{summary}"


_engine: SimulatorEngine | None = None


def get_simulator(polling_interval: int = 60, auto_start: bool = True) -> SimulatorEngine:
    global _engine
    if _engine is None:
        _engine = SimulatorEngine(polling_interval=polling_interval)
        if auto_start:
            _engine.start()
    return _engine
