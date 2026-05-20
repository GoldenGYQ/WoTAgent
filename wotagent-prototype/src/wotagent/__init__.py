"""WoTAgent — LangChain-powered Web of Things agent with event-driven architecture.

Event-driven architecture:
  Agent produces events → EventBus → Logging / SSE / Frontend
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Monkey-patch langchain-openai's message converters so DeepSeek's
# ``reasoning_content`` field survives the LLM call cycle.
#
# DeepSeek thinking mode with tool calls requires ``reasoning_content`` to
# be echoed back on subsequent requests.  Standard ``ChatOpenAI`` drops it.
# This patch captures it into ``additional_kwargs`` on parsing, and
# re-injects it when serializing back to API format.
# ---------------------------------------------------------------------------
import langchain_openai.chat_models.base as _chat_base

_original_convert_dict = _chat_base._convert_dict_to_message


def _patched_convert_dict(data):
    msg = _original_convert_dict(data)
    rc = data.get("reasoning_content")
    if rc is not None:
        msg.additional_kwargs["reasoning_content"] = rc
    return msg


_original_convert_message = _chat_base._convert_message_to_dict


def _patched_convert_message(message, **kwargs):
    d = _original_convert_message(message, **kwargs)
    if hasattr(message, "additional_kwargs") and "reasoning_content" in message.additional_kwargs:
        d["reasoning_content"] = message.additional_kwargs["reasoning_content"]
    return d


_chat_base._convert_dict_to_message = _patched_convert_dict
_chat_base._convert_message_to_dict = _patched_convert_message

# Also patch the streaming delta converter
_original_convert_delta = _chat_base._convert_delta_to_message_chunk


def _patched_convert_delta(_dict, default_class):
    msg = _original_convert_delta(_dict, default_class)
    rc = _dict.get("reasoning_content")
    if rc is not None:
        msg.additional_kwargs["reasoning_content"] = rc
    return msg


_chat_base._convert_delta_to_message_chunk = _patched_convert_delta

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
