"""Structured logging with event bus integration.

Every log entry with ``wotagent.`` prefix is also emitted as a
``wot.system.log`` event so the frontend can consume it.

Two output modes:
- ``console`` (default) — logs to stderr
- ``file`` — logs to ``wotagent.log`` in the CWD
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from ..events import Event, get_bus

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
def _project_root() -> Path:
    """Return the project root directory (where .env lives)."""
    return Path(__file__).resolve().parents[3]


_DEFAULT_LOG_FILE = str(_project_root() / "wotagent.log")


def configure_logging(
    level: int = logging.INFO,
    mode: str = "console",
    log_file: str | None = None,
) -> None:
    """Configure root logger.

    Args:
        level: Logging level.
        mode: ``"console"`` (stderr) or ``"file"`` (``wotagent.log`` in CWD).
        log_file: Override log file path (only used when mode is ``"file"``).
    """
    if mode == "file":
        path = Path(log_file or _DEFAULT_LOG_FILE).resolve()
        os.makedirs(path.parent, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S"))
    logging.basicConfig(level=level, handlers=[handler], force=True)


class EventBusHandler(logging.Handler):
    """Logging handler that forwards records to the event bus."""

    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._bus = get_bus()

    def emit(self, record: logging.LogRecord) -> None:
        import asyncio

        try:
            data: dict[str, Any] = {
                "logger": record.name,
                "level": record.levelname,
                "message": self.format(record),
            }
            if record.exc_info and record.exc_info[1]:
                data["exception"] = repr(record.exc_info[1])

            event = Event(
                source="wotagent/logger",
                type="wot.system.log",
                data=data,
            )
            asyncio.ensure_future(self._bus.emit(event))
        except Exception:
            pass  # don't let logging itself crash


def install_event_bus_handler(level: int = logging.INFO) -> None:
    """Install the event-bus log handler on the ``wotagent`` logger."""
    logger = logging.getLogger("wotagent")
    logger.addHandler(EventBusHandler(level))


def _summarize_data(data: dict[str, Any], max_len: int = 200) -> str:
    """Summarize event data into a log-friendly string."""
    parts = []
    for k in ("content", "message", "response", "input", "output", "error", "device", "action"):
        v = data.get(k)
        if v is not None:
            s = str(v)[:max_len]
            parts.append(f"{k}={s}")
    if not parts:
        s = str(data)[:max_len]
        if s and s != "{}":
            parts.append(s)
    return " | ".join(parts)


# Only log high-level semantic events — skip raw LangChain internals
_LOGGED_EVENT_TYPES: set[str] = {
    "wot.session.started",
    "wot.session.ended",
    "wot.agent.thought",
    "wot.agent.action.started",
    "wot.agent.action.completed",
    "wot.agent.action.failed",
    "wot.agent.response",
    "wot.agent.error",
    "wot.device.state_changed",
    "wot.device.discovered",
}


def install_event_logger() -> None:
    """Subscribe to the event bus and log high-level ``wot.*`` events.

    Skips internal ``wot.system.log`` noise (``on_chain_start`` /
    ``on_chain_stream`` / etc.) so the log file stays clean and readable.
    """
    bus = get_bus()
    log = logging.getLogger("wotagent.events")

    def _on_event(event: Event) -> None:
        if event.type not in _LOGGED_EVENT_TYPES:
            return
        summary = _summarize_data(event.data)
        line = "[{}]{} {}{}".format(
            event.type,
            f" <{event.subject}>" if event.subject else "",
            event.source,
            f" | {summary}" if summary else "",
        )
        log.info(line)

    bus.subscribe(_on_event)
