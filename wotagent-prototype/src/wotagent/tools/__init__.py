"""MCP transport used by the WoTAgent 2.0 device gateway."""

from .mcp_client import MCPToolClient, get_mcp_client

__all__ = ["MCPToolClient", "get_mcp_client"]
