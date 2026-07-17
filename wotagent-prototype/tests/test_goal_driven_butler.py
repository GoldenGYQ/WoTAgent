from __future__ import annotations

import asyncio

from wotagent.agentic_config import AgenticSettings
from wotagent.domains.eldercare import EldercareActionService, EldercareStore, Resident, RiskEvent
from wotagent.domains.eldercare.goal_graph import build_goal_driven_butler_graph
from wotagent.domains.eldercare.models import (
    AgentFinding,
    Delegation,
    Evidence,
    GoalEvaluation,
    GoalPlan,
)


class FakeHealthAgent:
    def __init__(self, finding: AgentFinding | None = None) -> None:
        self.calls = 0
        self.finding = finding

    async def run(self, *, task_id, resident_id, delegation):
        self.calls += 1
        return self.finding or AgentFinding(
            agent="health_agent",
            objective=delegation.objective,
            status="completed",
            finding="发现持续低血氧，需要护理人员复核。",
            confidence=0.95,
            evidence=[Evidence(
                source="read_vital_trend",
                value={"spo2": [91, 90, 89]},
                reliability=1.0,
                description="最近血氧趋势",
            )],
            recommended_actions=["通知护理员"],
        )


def setup(tmp_path):
    store = EldercareStore(tmp_path / "eldercare.db")
    store.upsert_resident(Resident(
        resident_id="elderly-001",
        name="张爷爷",
        room="A-101",
        caregiver="王护理员",
    ))
    return store, EldercareActionService(store)


def low_spo2_event() -> RiskEvent:
    return RiskEvent(
        resident_id="elderly-001",
        event_type="low_spo2",
        severity="high",
        value=89,
        message="血氧低于安全阈值",
    )


class GoalPlanner:
    async def __call__(self, _event, _agents):
        return GoalPlan(
            goal="核实并处置低血氧风险",
            delegations=[Delegation(
                agent="health_agent",
                objective="核实最新血氧和趋势",
                reason="先调查再处置",
                expected_evidence=["最新值", "趋势"],
            )],
            success_criteria=["健康证据充分", "护理员已通知"],
            constraints=["不得诊断疾病"],
            max_iterations=4,
        )


class TwoRoundEvaluator:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, plan, findings, event, iteration, agents):
        self.calls += 1
        if not any(item.agent == "care_agent" for item in findings):
            return GoalEvaluation(
                goal_completed=False,
                satisfied_criteria=["健康证据充分"],
                unsatisfied_criteria=["护理员已通知"],
                next_delegations=[Delegation(
                    agent="care_agent",
                    objective="通知护理员并创建照护任务",
                    reason="Health Finding 发现持续低血氧",
                    expected_evidence=["通知结果", "任务编号"],
                )],
                final_reason="需要 Care Agent 继续处置",
            )
        return GoalEvaluation(
            goal_completed=True,
            satisfied_criteria=plan.success_criteria,
            unsatisfied_criteria=[],
            next_delegations=[],
            final_reason="健康证据和人工通知均已完成",
        )


def test_butler_uses_health_finding_for_second_round_care_delegation(tmp_path) -> None:
    store, actions = setup(tmp_path)
    evaluator = TwoRoundEvaluator()
    graph = build_goal_driven_butler_graph(
        actions,
        FakeHealthAgent(),
        settings=AgenticSettings(decision_mode="hybrid"),
        goal_planner=GoalPlanner(),
        goal_evaluator=evaluator,
    )

    result = asyncio.run(graph.ainvoke({
        "task_id": "",
        "user_role": "system",
        "decision_mode": "hybrid",
        "event": low_spo2_event().model_dump(mode="json"),
    }))

    assert result["status"] == "succeeded"
    assert result["iteration"] == 2
    assert evaluator.calls == 2
    assert {item["agent"] for item in result["findings"]} == {
        "health_agent", "care_agent",
    }
    assert {item["action"] for item in store.list_actions()} == {
        "notify_caregiver", "create_care_task",
    }


def test_invalid_goal_planner_falls_back_to_rule_strategy(tmp_path) -> None:
    _, actions = setup(tmp_path)

    async def invalid_planner(_event, _agents):
        raise ValueError("invalid structured output")

    graph = build_goal_driven_butler_graph(
        actions,
        FakeHealthAgent(),
        settings=AgenticSettings(decision_mode="hybrid"),
        goal_planner=invalid_planner,
    )
    result = asyncio.run(graph.ainvoke({
        "task_id": "",
        "user_role": "system",
        "decision_mode": "hybrid",
        "event": low_spo2_event().model_dump(mode="json"),
    }))

    assert result["fallback_reasons"] == [
        "goal_planner: invalid structured output",
    ]
    assert result["goal_plan"]["goal"].startswith("安全处置")


def test_goal_planner_timeout_falls_back_to_rule_strategy(tmp_path) -> None:
    _, actions = setup(tmp_path)

    async def timed_out_planner(_event, _agents):
        raise TimeoutError("model deadline exceeded")

    graph = build_goal_driven_butler_graph(
        actions,
        FakeHealthAgent(),
        settings=AgenticSettings(decision_mode="hybrid"),
        goal_planner=timed_out_planner,
    )
    result = asyncio.run(graph.ainvoke({
        "task_id": "",
        "user_role": "system",
        "decision_mode": "hybrid",
        "event": low_spo2_event().model_dump(mode="json"),
    }))

    assert result["status"] == "succeeded"
    assert "model deadline exceeded" in result["fallback_reasons"][0]


def test_notification_failure_uses_bounded_duty_caregiver_compensation(tmp_path) -> None:
    store, _ = setup(tmp_path)

    class FailingAssignedCaregiver(EldercareActionService):
        async def notify_caregiver(self, resident_id, message, urgency):
            return {
                "success": False,
                "verified": False,
                "error_code": "RECIPIENT_UNAVAILABLE",
                "error": "assigned caregiver unavailable",
                "retryable": True,
            }

    graph = build_goal_driven_butler_graph(
        FailingAssignedCaregiver(store),
        FakeHealthAgent(),
        settings=AgenticSettings(decision_mode="rule", max_compensations=1),
    )
    result = asyncio.run(graph.ainvoke({
        "task_id": "",
        "user_role": "system",
        "decision_mode": "rule",
        "event": low_spo2_event().model_dump(mode="json"),
    }))

    assert result["status"] == "succeeded"
    assert {item["action"] for item in store.list_actions()} == {
        "notify_duty_caregiver",
        "create_care_task",
    }
    care = [item for item in result["findings"] if item["agent"] == "care_agent"]
    assert len(care) == 2
    assert [item["tool_name"] for item in care[0]["tool_calls"]] == [
        "notify_caregiver",
    ]
    assert [item["tool_name"] for item in care[1]["tool_calls"]] == [
        "notify_duty_caregiver",
        "create_care_task",
    ]


def test_viewer_cannot_bypass_rbac_with_llm_delegation(tmp_path) -> None:
    store, actions = setup(tmp_path)

    class DirectCarePlanner:
        async def __call__(self, _event, _agents):
            return GoalPlan(
                goal="直接通知",
                delegations=[Delegation(
                    agent="care_agent",
                    objective="通知护理员",
                    reason="模型建议",
                )],
                success_criteria=["护理员已通知"],
            )

    graph = build_goal_driven_butler_graph(
        actions,
        FakeHealthAgent(),
        settings=AgenticSettings(decision_mode="hybrid", butler_max_iterations=1),
        goal_planner=DirectCarePlanner(),
    )
    result = asyncio.run(graph.ainvoke({
        "task_id": "",
        "user_role": "viewer",
        "decision_mode": "hybrid",
        "event": RiskEvent(
            resident_id="elderly-001",
            event_type="user_request",
            severity="medium",
            value=True,
            message="通知护理员",
        ).model_dump(mode="json"),
    }))

    assert result["status"] == "failed"
    assert store.list_actions() == []
    assert result["findings"][0]["status"] == "blocked"


def test_unverified_environment_action_does_not_complete_goal(tmp_path) -> None:
    store, _ = setup(tmp_path)

    class UnverifiedActions(EldercareActionService):
        async def adjust_temperature(self, resident_id, target_temperature, reason):
            return {
                "success": True,
                "verified": False,
                "target_temperature": target_temperature,
            }

    class EnvironmentPlanner:
        async def __call__(self, _event, _agents):
            return GoalPlan(
                goal="降低房间温度",
                delegations=[Delegation(
                    agent="environment_agent",
                    objective="调温并验证",
                    reason="房间过热",
                )],
                success_criteria=["房间温度已验证"],
                max_iterations=1,
            )

    graph = build_goal_driven_butler_graph(
        UnverifiedActions(store),
        FakeHealthAgent(),
        settings=AgenticSettings(decision_mode="hybrid", butler_max_iterations=1),
        goal_planner=EnvironmentPlanner(),
        goal_evaluator=TwoRoundEvaluator(),
    )
    result = asyncio.run(graph.ainvoke({
        "task_id": "",
        "user_role": "system",
        "decision_mode": "hybrid",
        "event": RiskEvent(
            resident_id="elderly-001",
            event_type="user_request",
            severity="medium",
            value=True,
            message="调低房间温度",
        ).model_dump(mode="json"),
    }))

    assert result["status"] != "succeeded"
    environment = next(
        item for item in result["findings"]
        if item["agent"] == "environment_agent"
    )
    assert environment["status"] == "failed"
    assert environment["tool_calls"][0]["verified"] is False


def test_butler_stops_at_global_max_iterations(tmp_path) -> None:
    _, actions = setup(tmp_path)

    class BoundedPlanner:
        async def __call__(self, _event, _agents):
            return GoalPlan(
                goal="收集多领域证据",
                delegations=[Delegation(
                    agent="health_agent",
                    objective="第一轮健康调查",
                    reason="建立证据",
                )],
                success_criteria=["永远不会满足的条件"],
                max_iterations=8,
            )

    class NeverCompleteEvaluator:
        def __init__(self):
            self.calls = 0

        async def __call__(self, *_args):
            self.calls += 1
            return GoalEvaluation(
                goal_completed=False,
                satisfied_criteria=[],
                unsatisfied_criteria=["永远不会满足的条件"],
                next_delegations=[Delegation(
                    agent="care_agent",
                    objective=f"第 {self.calls} 次补充调查",
                    reason="证据仍不足",
                )],
                final_reason="继续调查",
            )

    evaluator = NeverCompleteEvaluator()
    graph = build_goal_driven_butler_graph(
        actions,
        FakeHealthAgent(),
        settings=AgenticSettings(
            decision_mode="hybrid",
            butler_max_iterations=2,
        ),
        goal_planner=BoundedPlanner(),
        goal_evaluator=evaluator,
    )
    result = asyncio.run(graph.ainvoke({
        "task_id": "",
        "user_role": "system",
        "decision_mode": "hybrid",
        "event": RiskEvent(
            resident_id="elderly-001",
            event_type="user_request",
            severity="medium",
            value=True,
            message="收集状态",
        ).model_dump(mode="json"),
    }))

    assert result["iteration"] == 2
    assert result["evaluation"]["goal_completed"] is False
    assert result["evaluation"]["next_delegations"] == []
    assert "最大迭代次数" in result["evaluation"]["final_reason"]
