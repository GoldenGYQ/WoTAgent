"""Model runtime assembly tests for Agent graph roles."""

from wotagent.core import LLMRuntime, create_llm_runtime


def test_model_runtime_contains_only_named_model_roles() -> None:
    models = create_llm_runtime(enable_thinking=False, temperature=0)

    assert isinstance(models, LLMRuntime)
    assert hasattr(models.planner, "ainvoke")
    assert hasattr(models.response_model, "ainvoke")
    assert models.thinking_planner is None
    assert models.thinking_response_model is None
    assert not hasattr(models, "executor")
    assert not hasattr(models, "observer")


def test_model_runtime_can_add_separate_thinking_models() -> None:
    models = create_llm_runtime(enable_thinking=True, temperature=0)

    assert models.thinking_planner is not None
    assert models.thinking_response_model is not None
