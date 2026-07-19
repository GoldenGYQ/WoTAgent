from __future__ import annotations

from wotagent.agents import AGENT_SPECS
from wotagent.auth import RelationshipAuthorizationService
from wotagent.identity import DEFAULT_PRINCIPAL_DIRECTORY
from wotagent.orchestration import TaskTarget


def test_human_domain_roles_are_separate_from_software_agents() -> None:
    caregiver = DEFAULT_PRINCIPAL_DIRECTORY.resolve("JH-1", "eldercare")
    owner = DEFAULT_PRINCIPAL_DIRECTORY.resolve("JH-1", "smart_home")
    security_staff = DEFAULT_PRINCIPAL_DIRECTORY.resolve(
        "JH-2",
        "smart_home",
    )

    assert caregiver.domain_roles == ["caregiver"]
    assert owner.domain_roles == ["villa_owner"]
    assert security_staff.domain_roles == ["security_staff"]
    assert "security_staff" not in AGENT_SPECS
    assert AGENT_SPECS["security_agent"].capability_category == (
        "security_response"
    )


def test_resident_can_be_a_human_principal_and_protected_subject() -> None:
    resident = DEFAULT_PRINCIPAL_DIRECTORY.resolve(
        "elderly-002",
        "eldercare",
    )

    assert resident.principal_type == "human"
    assert resident.domain_roles == ["resident"]
    assert resident.relationships[0].relation == "self"
    assert resident.relationships[0].resource_id == "resident:elderly-002"


def test_effective_authorization_is_principal_capability_and_resource() -> None:
    service = RelationshipAuthorizationService({})
    caregiver = DEFAULT_PRINCIPAL_DIRECTORY.resolve("JH-1", "eldercare")

    allowed = service.check(
        principal=caregiver,
        capability="health.summary.read",
        target=TaskTarget(type="resident", id="elderly-001"),
    )
    cross_resident = service.check(
        principal=caregiver,
        capability="health.summary.read",
        target=TaskTarget(type="resident", id="elderly-002"),
    )
    medication = service.check(
        principal=caregiver,
        capability="medication.adjust",
        target=TaskTarget(type="resident", id="elderly-001"),
    )

    assert allowed.effect == "allow"
    assert cross_resident.effect == "deny"
    assert medication.effect == "deny"


def test_principal_api_returns_domain_specific_human_context() -> None:
    from fastapi.testclient import TestClient

    from wotagent.api.app import app

    with TestClient(app) as client:
        response = client.get(
            "/api/v2/principals",
            params={"domain_id": "smart_home"},
        )

    assert response.status_code == 200
    contexts = {
        item["principal_id"]: item
        for item in response.json()["principals"]
    }
    assert contexts["JH-1"]["domain_roles"] == ["villa_owner"]
    assert contexts["JH-2"]["domain_roles"] == ["security_staff"]
    assert contexts["JH-2"]["roles"] == ["viewer"]
