"""Logging configuration for the WoTAgent 2.0 service."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def configure_logging(
    level: int = logging.INFO,
    mode: str = "console",
    log_file: str | None = None,
) -> None:
    """Configure console or UTF-8 file logging."""
    if mode == "file":
        path = Path(log_file or (_project_root() / "wotagent.log")).resolve()
        os.makedirs(path.parent, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S"))
    logging.basicConfig(level=level, handlers=[handler], force=True)
