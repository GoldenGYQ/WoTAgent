"""Load .env before running tests."""
import importlib
from pathlib import Path

import pytest
from dotenv import load_dotenv

from wotagent.supervisor import select_rule_supervisor_route

_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, encoding="utf-8")


@pytest.fixture(autouse=True)
def deterministic_supervisor_for_tests(monkeypatch):
    """Keep the full suite offline; focused router tests provide their own Fake LLM."""
    app_module = importlib.import_module("wotagent.api.app")

    class RuleTestRouter:
        async def route(self, **kwargs):
            return select_rule_supervisor_route(
                kwargs["message"],
                kwargs["history"],
                kwargs["residents"],
                excluded_agents=set(kwargs.get("attempted_agents", [])),
            )

    monkeypatch.setattr(app_module, "_supervisor_router", RuleTestRouter())
