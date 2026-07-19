"""Device gateway contracts and adapters."""

from .base import DeviceGateway
from .mcp import MCPDeviceGateway, deployment_device_scope

__all__ = ["DeviceGateway", "MCPDeviceGateway", "deployment_device_scope"]
