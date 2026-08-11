"""DeepSeek ``reasoning_content`` patch for ``langchain-openai``.

DeepSeek\'s thinking mode streams ``reasoning_content`` alongside tool calls.
Standard ``ChatOpenAI`` in ``langchain-openai`` drops this field during
message parsing/serialization.  This module captures it into
``additional_kwargs`` and re-injects it, preserving chain-of-thought
visibility across the LLM call cycle.

Usage:
    Importing this module applies the patches immediately::

        import wotagent._patch_deepseek  # noqa: F401

    Import it **before** any LangChain chat model calls.
"""

from __future__ import annotations

import langchain_openai.chat_models.base as _chat_base

# ---------------------------------------------------------------------------
# Helper: capture reasoning_content into additional_kwargs
# ---------------------------------------------------------------------------

def _capture_reasoning(msg, data):
    """Capture ``reasoning_content`` from raw API data into the message."""
    rc = data.get("reasoning_content")
    if rc is not None:
        msg.additional_kwargs["reasoning_content"] = rc
    return msg


# ---------------------------------------------------------------------------
# Patch 1: _convert_dict_to_message (non-streaming)
# ---------------------------------------------------------------------------

_original_convert_dict = _chat_base._convert_dict_to_message


def _patched_convert_dict(data):
    return _capture_reasoning(_original_convert_dict(data), data)


# ---------------------------------------------------------------------------
# Patch 2: _convert_message_to_dict (serialization)
# ---------------------------------------------------------------------------

_original_convert_message = _chat_base._convert_message_to_dict


def _patched_convert_message(message, **kwargs):
    d = _original_convert_message(message, **kwargs)
    if hasattr(message, "additional_kwargs") and "reasoning_content" in message.additional_kwargs:
        d["reasoning_content"] = message.additional_kwargs["reasoning_content"]
    return d


# ---------------------------------------------------------------------------
# Patch 3: _convert_delta_to_message_chunk (streaming)
# ---------------------------------------------------------------------------

_original_convert_delta = _chat_base._convert_delta_to_message_chunk


def _patched_convert_delta(_dict, default_class):
    return _capture_reasoning(_original_convert_delta(_dict, default_class), _dict)


# ---------------------------------------------------------------------------
# Apply patches
# ---------------------------------------------------------------------------

_chat_base._convert_dict_to_message = _patched_convert_dict
_chat_base._convert_message_to_dict = _patched_convert_message
_chat_base._convert_delta_to_message_chunk = _patched_convert_delta
