"""MCP client — ported from IOTAgent with LangChain tool adaptation.

Supports both HTTP and stdio transport modes via FastMCP Client.

Key design:
- Connection is kept alive across tool calls (no ``async with`` per call)
- Timeout on every tool call prevents hangs
- On timeout / error, the caller falls back to local implementation
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastmcp import Client

logger = logging.getLogger(__name__)

_MCP_CALL_TIMEOUT = 10.0  # seconds per tool call


def _get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_simulator_mcp_file() -> Path:
    simulator_server = (
        _get_project_root().parent
        / "wot-device-simulator"
        / "src"
        / "wot_device_simulator"
        / "mcp_server.py"
    )
    if simulator_server.exists():
        return simulator_server
    return _get_project_root() / "mcp_server" / "server.py"


def _normalize_tool(tool_obj: Any) -> dict[str, Any]:
    return {
        "name": getattr(tool_obj, "name", str(tool_obj)),
        "description": getattr(tool_obj, "description", ""),
        "title": getattr(tool_obj, "title", None),
        "input_schema": getattr(tool_obj, "inputSchema", {}) or {},
        "output_schema": getattr(tool_obj, "outputSchema", None),
    }


def _extract_text_from_content(content: Any) -> list[str]:
    text_fragments = []
    if isinstance(content, list):
        for entry in content:
            if getattr(entry, "type", None) == "text" and hasattr(entry, "text"):
                text_fragments.append(entry.text)
            elif isinstance(entry, dict) and "text" in entry:
                text_fragments.append(str(entry["text"]))
            else:
                text_fragments.append(str(entry))
    elif content is not None:
        text_fragments.append(str(content))
    return text_fragments


class MCPToolClient:
    """MCP tool client — persistent connection, no process-per-call.

    Connection is established once and kept alive.  On failure a warning is
    logged, the internal Client is rebuilt, and the caller falls back to local
    implementation.  A subsequent call will attempt to reconnect.
    """

    def __init__(
        self,
        server_url: str | None = None,
        server_file: str | None = None,
    ):
        self._server_url = server_url
        self._server_file = server_file
        self._client: Client | None = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._warned = False  # log warning only once

    def _make_client(self) -> Client:
        if self._server_url:
            return Client(self._server_url)
        if self._server_file:
            p = Path(self._server_file)
            if not p.is_absolute():
                p = _get_project_root() / self._server_file
            return Client(p)
        return Client(_default_simulator_mcp_file())

    async def connect(self) -> None:
        """Start the connection."""
        if self._connected:
            return
        async with self._lock:
            if self._connected:
                return
            # Rebuild client if previous attempt left it in a bad state
            if self._client is None:
                self._client = self._make_client()
            try:
                await self._client.__aenter__()
                self._connected = True
                logger.info("MCP client connected")
                self._warned = False
            except Exception:
                # Discard failed client so next connect() starts fresh
                self._client = None
                self._connected = False
                if not self._warned:
                    self._warned = True
                    logger.warning("MCP client connection failed — using local fallback")
                raise

    async def disconnect(self) -> None:
        """Close the connection."""
        if not self._connected:
            return
        async with self._lock:
            if not self._connected:
                return
            try:
                if self._client is not None:
                    await self._client.__aexit__(None, None, None)
            except Exception:
                logger.exception("MCP client disconnect error")
            finally:
                self._client = None
                self._connected = False
                logger.info("MCP client disconnected")

    async def disconnect(self) -> None:
        """Close the connection."""
        if not self._connected:
            return
        async with self._lock:
            if not self._connected:
                return
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                logger.exception("MCP client disconnect error")
            finally:
                self._connected = False
                logger.info("MCP client disconnected")

    async def list_tools(self) -> list[dict[str, Any]]:
        if not self._connected:
            await self.connect()
        async with self._lock:
            response = await self._client.list_tools()
        normalized = []
        if isinstance(response, list):
            for item in response:
                if isinstance(item, tuple) and item[0] == "tools":
                    normalized.extend(_normalize_tool(t) for t in item[1])
                else:
                    normalized.append(_normalize_tool(item))
        else:
            try:
                normalized = [_normalize_tool(t) for t in iter(response)]
            except TypeError:
                normalized = [_normalize_tool(response)]
        return normalized

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float = _MCP_CALL_TIMEOUT,
    ) -> dict[str, Any]:
        """Call an MCP tool with timeout.

        Raises ``asyncio.TimeoutError`` if the call takes longer than *timeout*.
        """
        if not self._connected:
            await self.connect()
        if self._client is None:
            raise ConnectionError("MCP client not available")
        async with self._lock:
            response = await asyncio.wait_for(
                self._client.call_tool(tool_name, arguments),
                timeout=timeout,
            )
        content = getattr(response, "content", None)
        text_fragments = _extract_text_from_content(content) or [str(response)]
        text_output = "\n".join(f for f in text_fragments if f).strip()
        return {
            "text": text_output,
            "structured": getattr(response, "structuredContent", None),
            "raw": response,
        }


# Global singleton
_mcp_client: MCPToolClient | None = None


async def get_mcp_client(
    server_url: str | None = None,
    transport_mode: str | None = None,
) -> MCPToolClient:
    """Get or create the singleton MCP client.

    The connection is kept alive once established and reused across all tool calls.
    Call ``reset_mcp_client()`` to force a reconnect.
    """
    global _mcp_client
    if _mcp_client is None:
        mode = transport_mode or os.getenv("MCP_TRANSPORT_MODE", "stdio")
        if mode.lower() == "http":
            url = server_url or os.getenv("MCP_SERVER_URL", "http://localhost:8000/mcp")
            _mcp_client = MCPToolClient(server_url=url)
        else:
            file_path = os.getenv("MCP_SERVER_FILE", "")
            _mcp_client = MCPToolClient(server_file=file_path or None)
    return _mcp_client


def reset_mcp_client() -> None:
    global _mcp_client
    _mcp_client = None
