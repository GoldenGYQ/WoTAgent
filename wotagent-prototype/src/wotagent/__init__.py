"""WoTAgent -- LangChain-powered Web of Things agent with event-driven architecture.

Event-driven architecture:
    Agent produces events -> EventBus -> Logging / WebSocket / Frontend
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# DeepSeek ``reasoning_content`` patch -- isolated in a dedicated module
# so upgrades to ``langchain-openai`` are easier to track.
# ---------------------------------------------------------------------------
from . import _patch_deepseek  # noqa: F401
# ---------------------------------------------------------------------------

from . import auth
from . import core
from . import events
from . import memory
from . import perception
from . import prompts
from . import rag
from . import tools
from . import wot

__version__ = "0.3.0"
__all__ = ["auth", "core", "events", "memory", "perception", "prompts", "rag", "tools", "wot"]
