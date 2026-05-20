"""Tool registry — central discovery and lifecycle for all agent tools.

Combines:
1. IoT tools (based on WoT TD)
2. MCP tools (dynamically discovered from MCP servers)
3. System tools (file ops, system info ported from IOTAgent)
"""

from __future__ import annotations

import os
import platform
from datetime import datetime
from typing import Any

from langchain_core.tools import StructuredTool

from .iot import get_iot_tools

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ---------------------------------------------------------------------------
# System tool implementations  (ported from IOTAgent)
# ---------------------------------------------------------------------------

def _get_system_info() -> dict[str, Any]:
    """Get system information (CPU, memory, disk usage, platform)."""
    info: dict[str, Any] = {
        "platform": platform.system(),
        "platform_version": platform.version(),
        "timestamp": datetime.now().isoformat(),
    }
    if HAS_PSUTIL:
        info.update({
            "cpu_count": psutil.cpu_count(),
            "cpu_percent": psutil.cpu_percent(interval=0.5),
            "memory_total_gb": round(psutil.virtual_memory().total / (1024 ** 3), 2),
            "memory_used_gb": round(psutil.virtual_memory().used / (1024 ** 3), 2),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_usage_percent": (
                psutil.disk_usage("/").percent if os.name != "nt"
                else psutil.disk_usage("C:\\").percent
            ),
        })
    return info


def _list_files(directory: str = ".") -> list[str]:
    """List files in a directory.

    Args:
        directory: Directory path (default: current).
    """
    try:
        entries = []
        for item in os.listdir(directory):
            item_path = os.path.join(directory, item)
            if os.path.isfile(item_path):
                size = os.path.getsize(item_path)
                entries.append(f"{item} ({size} bytes)")
            else:
                entries.append(f"{item}/")
        return entries
    except Exception as e:
        return [f"Error: {e}"]


def _read_file(filepath: str) -> str:
    """Read the contents of a text file.

    Args:
        filepath: Path to the file.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Cannot read file: {e}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def get_system_tools() -> list[StructuredTool]:
    """Return system-level LangChain tools."""
    tools = [
        StructuredTool.from_function(
            func=_get_system_info,
            name="get_system_info",
            description="Get system information including CPU, memory, disk usage, and platform details.",
        ),
        StructuredTool.from_function(
            func=_list_files,
            name="list_files",
            description="List files and directories at the specified path.",
        ),
        StructuredTool.from_function(
            func=_read_file,
            name="read_file",
            description="Read the contents of a text file.",
        ),
    ]
    return tools


def get_all_tools(include_mcp: bool = False) -> list[StructuredTool]:
    """Get all available tools: IoT + System + optionally MCP."""
    tools: list[StructuredTool] = []
    tools.extend(get_iot_tools())
    tools.extend(get_system_tools())
    return tools
