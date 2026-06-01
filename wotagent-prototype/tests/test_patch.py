"""Tests for the DeepSeek reasoning_content patch module."""

from __future__ import annotations


class TestPatchModule:
    """Verify the patch module exists and can be imported safely."""

    def test_patch_importable(self):
        """Patch module imports without error."""
        import wotagent._patch_deepseek  # noqa: F401

    def test_patches_applied(self):
        """All expected patches are applied correctly."""
        import wotagent._patch_deepseek  # noqa: F401
        import langchain_openai.chat_models.base as base

        orig_convert = base._convert_dict_to_message
        # The patched function should NOT be the original one
        # (original was stored as _original_convert_dict at module level)
        import wotagent._patch_deepseek as patch_mod

        assert base._convert_dict_to_message is patch_mod._patched_convert_dict
        assert base._convert_message_to_dict is patch_mod._patched_convert_message
        assert base._convert_delta_to_message_chunk is patch_mod._patched_convert_delta

    def test_capture_reasoning_content(self):
        """Reasoning content is captured from DeepSeek responses."""
        from wotagent._patch_deepseek import _capture_reasoning

        class FakeMessage:
            def __init__(self):
                self.additional_kwargs = {}

        msg = FakeMessage()
        data = {"reasoning_content": "thinking step by step"}
        result = _capture_reasoning(msg, data)
        assert result.additional_kwargs["reasoning_content"] == "thinking step by step"

    def test_capture_no_reasoning(self):
        """Responses without reasoning return empty string."""
        from wotagent._patch_deepseek import _capture_reasoning

        class FakeMessage:
            def __init__(self):
                self.additional_kwargs = {}

        msg = FakeMessage()
        result = _capture_reasoning(msg, {"other": "data"})
        assert "reasoning_content" not in result.additional_kwargs
