"""Model assembly tests for the 2.0 graph roles."""

from wotagent.core import AgentPipeline, create_wot_pipeline


def test_pipeline_contains_only_graph_model_roles() -> None:
    pipeline = create_wot_pipeline(enable_thinking=False, temperature=0)

    assert isinstance(pipeline, AgentPipeline)
    assert hasattr(pipeline.planner, "ainvoke")
    assert hasattr(pipeline.response_model, "ainvoke")
    assert pipeline.thinking_planner is None
    assert pipeline.thinking_response_model is None
    assert not hasattr(pipeline, "executor")
    assert not hasattr(pipeline, "observer")


def test_pipeline_can_add_separate_thinking_models() -> None:
    pipeline = create_wot_pipeline(enable_thinking=True, temperature=0)

    assert pipeline.thinking_planner is not None
    assert pipeline.thinking_response_model is not None
