"""Stable project paths that do not depend on package nesting depth."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def project_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("cannot locate WoTAgent project root")


def data_path(name: str) -> Path:
    return project_root() / "data" / name


__all__ = ["data_path", "project_root"]
