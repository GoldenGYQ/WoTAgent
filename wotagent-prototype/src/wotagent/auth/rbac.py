"""RBAC module — role-based access control for agent actions.

Inspired by the existing prototype but extended with:
- Role hierarchy (admin > operator > viewer)
- Domain-level resource matching
- Permission decorators for tool integration
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


# Role hierarchy — higher index = more privilege
_ROLE_HIERARCHY: list[Role] = [Role.VIEWER, Role.OPERATOR, Role.ADMIN]


@dataclass(frozen=True)
class Permission:
    resource: str   # e.g. "device.control", "device.read", "session.list"
    action: str     # e.g. "read", "write", "execute"


@dataclass
class RolePermissions:
    role: Role
    permissions: set[Permission] = field(default_factory=set)

    def can(self, resource: str, action: str) -> bool:
        return Permission(resource, action) in self.permissions


# ---------------------------------------------------------------------------
# Default permission sets
# ---------------------------------------------------------------------------

_ADMIN_PERMS = {
    Permission("*", "*"),
}

_OPERATOR_PERMS = {
    Permission("device.control", "execute"),
    Permission("device.read", "read"),
    Permission("session.own", "read"),
    Permission("rag.query", "read"),
}

_VIEWER_PERMS = {
    Permission("device.read", "read"),
    Permission("rag.query", "read"),
}

DEFAULT_ROLE_PERMISSIONS: dict[Role, RolePermissions] = {
    Role.ADMIN: RolePermissions(Role.ADMIN, _ADMIN_PERMS),
    Role.OPERATOR: RolePermissions(Role.OPERATOR, _OPERATOR_PERMS),
    Role.VIEWER: RolePermissions(Role.VIEWER, _VIEWER_PERMS),
}


# ---------------------------------------------------------------------------
# Permission checker
# ---------------------------------------------------------------------------

class AccessControl:
    """Fine-grained access control with role hierarchy."""

    def __init__(self, role_permissions: dict[Role, RolePermissions] | None = None) -> None:
        self._role_perms = role_permissions or DEFAULT_ROLE_PERMISSIONS

    def check(self, role: Role | str, resource: str, action: str) -> bool:
        """Check if *role* can perform *action* on *resource*."""
        if isinstance(role, str):
            role = Role(role)
        rp = self._role_perms.get(role)
        if rp is None:
            return False

        # Direct match
        if rp.can(resource, action):
            return True

        # Wildcard match: check "resource.*" or "*.action" or "*.*"
        for p in rp.permissions:
            if _match(p.resource, resource) and _match(p.action, action):
                return True

        return False

    def require(self, resource: str, action: str) -> Callable:
        """Decorator that checks permission before calling a tool function."""
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args: Any, role: str = "admin", **kwargs: Any) -> Any:
                if not self.check(role, resource, action):
                    raise PermissionError(
                        f"Role '{role}' lacks '{action}' on '{resource}'"
                    )
                return func(*args, **kwargs)
            return wrapper
        return decorator

    def require_role(self, min_role: Role | str) -> Callable[[Callable], Callable]:
        """Decorator that checks role hierarchy level."""
        if isinstance(min_role, str):
            min_role = Role(min_role)

        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*args: Any, role: Role = Role.ADMIN, **kwargs: Any) -> Any:
                if _role_level(role) < _role_level(min_role):
                    raise PermissionError(
                        f"Role '{role.value}' is below minimum '{min_role.value}'"
                    )
                return func(*args, **kwargs)
            return wrapper
        return decorator


def _match(pattern: str, actual: str) -> bool:
    """Match a pattern (with * wildcard) against an actual string."""
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return actual.startswith(pattern[:-1])
    return pattern == actual


def _role_level(role: Role) -> int:
    try:
        return _ROLE_HIERARCHY.index(role)
    except ValueError:
        return -1


# Singleton
_ac: AccessControl | None = None


def get_access_control() -> AccessControl:
    global _ac
    if _ac is None:
        _ac = AccessControl()
    return _ac
