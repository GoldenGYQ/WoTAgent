"""Temporary demo identity relationships used until real authentication exists."""

DEMO_RESIDENT_ASSIGNMENTS: dict[str, set[str]] = {
    "JH-1": {"elderly-001"},
    "JH-2": {"elderly-002"},
    "local-user": {"elderly-001", "elderly-002"},
    "system:eldercare-monitor": {"elderly-001", "elderly-002"},
}

__all__ = ["DEMO_RESIDENT_ASSIGNMENTS"]
