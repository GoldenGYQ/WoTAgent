from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from wotagent.domains.eldercare.entity_resolution import ResidentEntityResolver
from wotagent.domains.eldercare.health_gateway import ScopedHealthMCPClient
from wotagent.domains.eldercare.store import EldercareStore
from wotagent.domain import DeploymentProfileRegistry
from wotagent.identity import DEFAULT_PRINCIPAL_DIRECTORY
from wotagent.orchestration import (
    AgentTask,
    ResourceScope,
    TaskPreparationError,
    TaskPrincipal,
)
from wotagent.runtime.run_runtime import _task_preparer


def _store(tmp_path) -> EldercareStore:
    store = EldercareStore(tmp_path / "eldercare.db")
    store.seed_demo()
    return store


def _health_task(principal_id: str) -> AgentTask:
    return AgentTask(
        task_id=f"task-{principal_id}",
        run_id="run-1",
        local_id="health",
        agent_id="health_agent",
        objective="查看张爷爷今天的健康状态",
        reason="用户请求",
        success_criteria=["获得健康摘要"],
        principal=TaskPrincipal(
            principal_id=principal_id,
            roles=["operator" if principal_id == "JH-1" else "viewer"],
        ),
        target_type="resident",
        target_mention="张爷爷",
        requested_capability="health.status.read",
    )


def test_resident_entity_resolver_returns_canonical_id(tmp_path) -> None:
    target = ResidentEntityResolver(_store(tmp_path)).resolve(
        target_type="resident",
        mention="张爷爷",
        text="查看张爷爷今天的健康状态",
    )

    assert target.id == "elderly-001"
    assert target.display_name == "张爷爷"


def test_task_preparer_issues_scope_for_assigned_resident(tmp_path) -> None:
    async def scenario():
        prepare = _task_preparer(SimpleNamespace(
            eldercare_store=_store(tmp_path),
        ))
        return await prepare(
            _health_task("JH-1"),
            "查看张爷爷今天的健康状态",
            {},
        )

    task = asyncio.run(scenario())

    assert task.target is not None
    assert task.target.id == "elderly-001"
    assert task.effective_scope is not None
    assert task.effective_scope.resource_ids == ["resident:elderly-001"]


def test_task_preparer_normalizes_model_proposed_health_capability(
    tmp_path,
) -> None:
    async def scenario():
        prepare = _task_preparer(SimpleNamespace(
            eldercare_store=_store(tmp_path),
        ))
        task = _health_task("JH-1")
        task.requested_capability = "health.read.generated.by.model"
        return await prepare(
            task,
            "查看张爷爷今天的健康状态",
            {},
        )

    task = asyncio.run(scenario())

    assert task.model_requested_capability == "health.read.generated.by.model"
    assert task.requested_capability == "health.status.read"
    assert task.effective_scope is not None
    assert task.effective_scope.capabilities == ["health.status.read"]


def test_task_preparer_normalizes_model_proposed_target_type(
    tmp_path,
) -> None:
    async def scenario():
        prepare = _task_preparer(SimpleNamespace(
            eldercare_store=_store(tmp_path),
        ))
        task = _health_task("JH-1")
        task.target_type = "elderly_person_generated_by_model"
        return await prepare(
            task,
            "查看张爷爷今天的健康状态",
            {},
        )

    task = asyncio.run(scenario())

    assert task.target_type == "resident"
    assert task.target is not None
    assert task.target.id == "elderly-001"


def test_task_preparer_denies_cross_resident_access(tmp_path) -> None:
    async def scenario():
        prepare = _task_preparer(SimpleNamespace(
            eldercare_store=_store(tmp_path),
        ))
        return await prepare(
            _health_task("JH-2"),
            "查看张爷爷今天的健康状态",
            {},
        )

    with pytest.raises(TaskPreparationError) as captured:
        asyncio.run(scenario())

    assert captured.value.failure.category == "authorization"
    assert captured.value.failure.code == "RESOURCE_ACCESS_DENIED"
    assert captured.value.failure.retryable is False


def test_health_gateway_rejects_resident_outside_effective_scope(
    tmp_path,
) -> None:
    async def scenario():
        gateway = ScopedHealthMCPClient(
            _store(tmp_path),
            require_scope=True,
        )
        with gateway.authorized_scope(ResourceScope(
            resource_ids=["resident:elderly-001"],
            capabilities=["health.status.read"],
        )):
            return await gateway.call(
                "read_health_summary",
                {"resident_id": "elderly-002"},
            )

    result = asyncio.run(scenario())

    assert result.success is False
    assert result.error_code == "RESOURCE_SCOPE_DENIED"
    assert result.retryable is False


def test_security_staff_can_delegate_observation_but_not_environment_control(
    tmp_path,
) -> None:
    profile = DeploymentProfileRegistry.load().require("villa")
    principal = DEFAULT_PRINCIPAL_DIRECTORY.resolve("JH-2", "smart_home")
    runtime = SimpleNamespace(eldercare_store=_store(tmp_path))
    prepare = _task_preparer(runtime)

    def task(agent_id: str, capability: str) -> AgentTask:
        return AgentTask(
            task_id=f"task-{agent_id}",
            run_id="run-villa",
            local_id=agent_id,
            agent_id=agent_id,
            objective="检查状态",
            reason="用户请求",
            success_criteria=["获得设备状态"],
            principal=principal,
            requested_capability=capability,
        )

    security = asyncio.run(prepare(
        task("security_agent", "security.observe"),
        "检查入口安防状态",
        {"deployment": profile.model_dump(mode="json")},
    ))

    assert security.effective_scope is not None
    assert security.effective_scope.capabilities == ["security.observe"]
    assert security.effective_scope.resource_ids

    with pytest.raises(TaskPreparationError) as captured:
        asyncio.run(prepare(
            task("environment_agent", "environment.use"),
            "打开客厅灯",
            {"deployment": profile.model_dump(mode="json")},
        ))
    assert captured.value.failure.code == "AGENT_CAPABILITY_NOT_DELEGABLE"


@pytest.mark.parametrize(
    ("agent_id", "model_proposal", "canonical"),
    [
        (
            "environment_agent",
            "控制客厅灯光并查询状态",
            "environment.use",
        ),
        (
            "device_ops_agent",
            "查询客厅灯光设备状态和TD",
            "device_ops.use",
        ),
    ],
)
def test_villa_owner_model_capability_proposal_is_canonicalized(
    tmp_path,
    agent_id: str,
    model_proposal: str,
    canonical: str,
) -> None:
    profile = DeploymentProfileRegistry.load().require("villa")
    principal = DEFAULT_PRINCIPAL_DIRECTORY.resolve("JH-1", "smart_home")
    prepare = _task_preparer(SimpleNamespace(
        eldercare_store=_store(tmp_path),
    ))
    task = AgentTask(
        task_id=f"task-{agent_id}",
        run_id="run-villa-owner",
        local_id=agent_id,
        agent_id=agent_id,
        objective="打开客厅灯并验证",
        reason="用户请求",
        success_criteria=["灯光状态已验证"],
        principal=principal,
        requested_capability=model_proposal,
        resource_claims=[{
            "key": "device:light-001",
            "mode": "write",
        }],
    )

    prepared = asyncio.run(prepare(
        task,
        "打开客厅灯并调到45%，然后验证",
        {"deployment": profile.model_dump(mode="json")},
    ))

    assert prepared.model_requested_capability == model_proposal
    assert prepared.requested_capability == canonical
    assert prepared.failure is None
    assert prepared.effective_scope is not None
    assert prepared.effective_scope.capabilities == [canonical]
