"""Device gateway contracts and adapters."""

from .base import DeviceGateway
from .mcp import MCPDeviceGateway

__all__ = ["DeviceGateway", "MCPDeviceGateway"]
