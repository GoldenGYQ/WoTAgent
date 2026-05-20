"""Thing Description loader — reads TD JSON files directly, no simulator dependency.

All device definitions come from ``wot-device-simulator/data/td/*.json`` (or a
configurable path via ``WOT_TD_ROOT`` env var).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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
    """WoTAgent monorepo root (two levels up from ``wotagent/wot/``)."""
    return Path(__file__).resolve().parents[3]


def td_root() -> Path:
    """Return the TD directory path.

    Order of precedence:
    1. ``WOT_TD_ROOT`` env var
    2. ``wot-device-simulator/data/td/`` (sibling project)
    """
    override = os.getenv("WOT_TD_ROOT", "").strip()
    if override:
        return Path(override)
    return _project_root().parent / "wot-device-simulator" / "data" / "td"


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


def load_tds() -> list[ThingDescription]:
    """Load all TD JSON files from ``td_root()``."""
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


# ---------------------------------------------------------------------------
# Location synonym map  —  common aliases → canonical TD location values
# ---------------------------------------------------------------------------

_LOCATION_SYNONYMS: dict[str, set[str]] = {
    "living_room": {"living_room", "living room", "parlor", "客厅", "起居室", "大堂"},
    "bedroom1": {"bedroom1", "bedroom 1", "主卧", "卧室1", "master bedroom"},
    "bedroom2": {"bedroom2", "bedroom 2", "次卧", "卧室2", "guest bedroom"},
    "bathroom1": {"bathroom1", "bathroom 1", "主卫", "卫生间1", "浴室1"},
    "bathroom2": {"bathroom2", "bathroom 2", "客卫", "卫生间2", "浴室2"},
    "kitchen": {"kitchen", "厨房"},
    "study": {"study", "书房", "studying room"},
}


def _match_location(td_loc: str, query: str) -> bool:
    if td_loc == query:
        return True
    # Check synonym groups
    for canonical, aliases in _LOCATION_SYNONYMS.items():
        if query in aliases:
            return td_loc == canonical
    # Prefix match: e.g. "bedroom" matches "bedroom1", "bedroom2"
    if td_loc.startswith(query) or query.startswith(td_loc):
        return True
    return False


def find_devices(
    capability: str | None = None,
    location: str | None = None,
    *,
    semantic: bool = False,
) -> list[ThingDescription]:
    """Search devices by capability and/or location.

    Args:
        capability: Filter by capability domain (e.g. ``"temperature"``).
        location: Filter by room (supports synonyms like ``"parlor"`` → ``"living_room"``).
        semantic: When True, also match capability via synonym groups.
    """
    capability = capability.lower() if capability else None
    location = location.lower() if location else None

    capability_synonyms: dict[str, set[str]] = {
        "luminance": {"light", "luminance", "brightness", "照明", "灯光"},
        "temperature": {"temperature", "temp", "温度", "温控"},
        "humidity": {"humidity", "湿度"},
    }

    results: list[ThingDescription] = []
    for td in load_tds():
        if capability:
            caps_set = set(c.lower() for c in td.capabilities)
            if capability not in caps_set:
                if not semantic:
                    continue
                syn = capability_synonyms.get(capability, set())
                if not caps_set & syn:
                    continue

        if location and not _match_location(td.location.lower(), location):
            continue

        results.append(td)

    return results


def get_device_by_id(device_id: str) -> ThingDescription | None:
    """Look up a device by its short ID (e.g. ``\"light-001\"``)."""
    for td in load_tds():
        if td.device_id == device_id or td.id == device_id:
            return td
    return None


__all__ = [
    "TDAction",
    "TDProperty",
    "ThingDescription",
    "load_tds",
    "find_devices",
    "get_device_by_id",
]
