from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from pydantic import BaseModel, Field


class TDAction(BaseModel):
    name: str
    input_type: str | None = None
    description: str = ""


class TDProperty(BaseModel):
    name: str
    type: str = "string"
    readable: bool = True
    writable: bool = False


class ThingDescription(BaseModel):
    id: str = ""
    title: str = ""
    description: str = ""
    location: str = ""
    capabilities: list[str] = Field(default_factory=list)
    actions: list[TDAction] = Field(default_factory=list)
    properties: list[TDProperty] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def device_id(self) -> str:
        return self.id.split(":")[-1] if ":" in self.id else self.id


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def td_root() -> Path:
    override = os.getenv("WOT_SIM_TD_ROOT", "").strip()
    if override:
        return Path(override)
    return _project_root() / "data" / "td"


def _parse_td(raw: dict[str, Any]) -> ThingDescription:
    actions = []
    for name, spec in (raw.get("actions") or {}).items():
        inp = spec.get("input", {}) if isinstance(spec, dict) else {}
        actions.append(TDAction(name=name, input_type=inp.get("type")))

    properties = []
    for name, spec in (raw.get("properties") or {}).items():
        if isinstance(spec, dict):
            properties.append(TDProperty(name=name, type=spec.get("type", "string")))

    return ThingDescription(
        id=raw.get("id", ""),
        title=raw.get("title", ""),
        description=raw.get("description", ""),
        location=str(raw.get("location", "")),
        capabilities=[c.lower() for c in (raw.get("capabilities") or [])],
        actions=actions,
        properties=properties,
        raw=raw,
    )


def load_tds_local() -> list[ThingDescription]:
    tds: list[ThingDescription] = []
    root = td_root()
    if not root.exists():
        return tds

    for fp in sorted(root.glob("*.json")):
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
            tds.append(_parse_td(raw))
        except (json.JSONDecodeError, KeyError):
            continue

    return tds


def load_tds_remote(base_url: str, timeout: float = 5.0) -> list[ThingDescription]:
    url = f"{base_url.rstrip('/')}/api/td"
    try:
        with urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError):
        return []

    items = payload.get("tds", []) if isinstance(payload, dict) else []
    tds: list[ThingDescription] = []
    for raw in items:
        if isinstance(raw, dict):
            tds.append(_parse_td(raw))
    return tds


def load_tds() -> list[ThingDescription]:
    registry = os.getenv("WOT_SIM_TD_REGISTRY", "").strip()
    if registry:
        remote = load_tds_remote(registry)
        if remote:
            return remote
    return load_tds_local()


def find_devices(
    capability: str | None = None,
    location: str | None = None,
    *,
    semantic: bool = False,
) -> list[ThingDescription]:
    capability = capability.lower() if capability else None
    location = location.lower() if location else None

    synonyms = {
        "luminance": {"light", "luminance", "brightness", "照明", "灯光"},
        "temperature": {"temperature", "temp", "温度", "温控"},
        "humidity": {"humidity", "湿度"},
    }

    # Location synonym map: common aliases → canonical TD location values
    location_synonyms: dict[str, set[str]] = {
        "living_room": {"living_room", "living room", "parlor", "客厅", "起居室", "大堂"},
        "bedroom1": {"bedroom1", "bedroom 1", "bedroom", "主卧", "卧室1", "master bedroom"},
        "bedroom2": {"bedroom2", "bedroom 2", "次卧", "卧室2", "guest bedroom"},
        "bathroom1": {"bathroom1", "bathroom 1", "bathroom", "主卫", "卫生间1", "浴室1"},
        "bathroom2": {"bathroom2", "bathroom 2", "客卫", "卫生间2", "浴室2"},
        "kitchen": {"kitchen", "厨房"},
        "study": {"study", "书房", "studying room"},
    }

    def _match_location(td_loc: str, query: str) -> bool:
        """Match a query location against a TD location, using synonyms and prefix."""
        if td_loc == query:
            return True
        # Check synonym groups
        for canonical, aliases in location_synonyms.items():
            if query in aliases:
                return td_loc == canonical
        # Prefix match: e.g. "bedroom" matches "bedroom1", "bedroom2"
        if td_loc.startswith(query) or query.startswith(td_loc):
            return True
        return False

    results: list[ThingDescription] = []
    for td in load_tds():
        if capability:
            caps_set = set(c.lower() for c in td.capabilities)
            if capability not in caps_set:
                if not semantic:
                    continue
                syn = synonyms.get(capability, set())
                if not caps_set & syn:
                    continue

        if location and not _match_location(td.location.lower(), location):
            continue

        results.append(td)

    return results


def get_device_by_id(device_id: str) -> ThingDescription | None:
    for td in load_tds():
        if td.device_id == device_id or td.id == device_id:
            return td
    return None
