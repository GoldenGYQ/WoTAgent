from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wotagent.api.routers.domains import create_domain_router
from wotagent.api.routers.principals import create_principal_router
from wotagent.domain import DeploymentProfileRegistry, DomainPluginRegistry
from wotagent.domains.smart_home import SmartHomeDomainPlugin
from wotagent.identity import (
    DEMO_PRINCIPALS,
    PrincipalAuthenticationError,
    PrincipalStore,
)


def _store(tmp_path) -> PrincipalStore:
    return PrincipalStore(
        tmp_path / "principals.db",
        seed_profiles=DEMO_PRINCIPALS,
    )


def test_registered_principal_and_binding_survive_store_restart(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    principal, token = store.register(
        display_name="周女士",
        pin="2468",
        domain_id="eldercare",
        role_template_id="eldercare_family_observer",
        target_id="elderly-001",
    )

    restarted = PrincipalStore(tmp_path / "principals.db")
    restored = restarted.resolve(principal.principal_id, "eldercare")
    session = restarted.resolve_session(token, "eldercare")

    assert restored.display_name == "周女士"
    assert restored.domain_roles == ["family_observer"]
    assert restored.resource_ids == ["resident:elderly-001"]
    assert restored.granted_capabilities == [
        "domain.read",
        "device.read",
        "health.status.read",
        "health.summary.read",
        "health.vitals.read",
        "health.trend.read",
        "health.data_quality.read",
        "resident.context.read",
    ]
    assert session.principal_id == principal.principal_id


def test_pin_principal_requires_valid_login_session(tmp_path) -> None:
    store = _store(tmp_path)
    principal, _ = store.register(
        display_name="新用户",
        pin="1357",
        domain_id="smart_home",
        role_template_id="villa_family_member",
        target_id="villa-demo",
    )

    with pytest.raises(PrincipalAuthenticationError):
        store.login(
            principal_id=principal.principal_id,
            domain_id="smart_home",
            pin="0000",
        )
    with pytest.raises(PrincipalAuthenticationError):
        store.resolve_for_request(
            principal_id=principal.principal_id,
            domain_id="smart_home",
            token=None,
            fallback_role="admin",
        )

    logged_in, token = store.login(
        principal_id=principal.principal_id,
        domain_id="smart_home",
        pin="1357",
    )
    resolved = store.resolve_for_request(
        principal_id=principal.principal_id,
        domain_id="smart_home",
        token=token,
        fallback_role="admin",
    )

    assert logged_in.roles == ["operator"]
    assert resolved.roles == ["operator"]
    assert "security.use" not in resolved.granted_capabilities


def test_password_principal_can_login_by_account_name(tmp_path) -> None:
    store = _store(tmp_path)

    principal, token = store.login(
        login_name="admin",
        domain_id="eldercare",
        password="Admin@123",
    )

    assert principal.roles == ["admin"]
    assert principal.login_name == "admin"
    assert store.resolve_session(token, "eldercare").principal_id == "ADMIN-1"


def test_principal_api_registers_from_template_and_rejects_raw_grants(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    app = FastAPI()
    app.include_router(create_principal_router(lambda: store))
    client = TestClient(app)

    rejected = client.post("/api/v2/principals/register", json={
        "display_name": "越权用户",
        "pin": "2468",
        "domain_id": "smart_home",
        "role_template_id": "villa_security_observer",
        "target_id": "villa-demo",
        "capabilities": ["*"],
    })
    assert rejected.status_code == 422

    registered = client.post("/api/v2/principals/register", json={
        "display_name": "观察用户",
        "pin": "2468",
        "domain_id": "smart_home",
        "role_template_id": "villa_security_observer",
        "target_id": "villa-demo",
    })
    assert registered.status_code == 201
    body = registered.json()
    assert body["principal"]["domain_roles"] == ["security_staff"]
    assert body["principal"]["granted_capabilities"] == [
        "domain.read",
        "device.read",
        "security.observe",
    ]

    session = client.get(
        "/api/v2/principals/session?domain_id=smart_home",
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert session.status_code == 200
    assert (
        session.json()["principal"]["principal_id"]
        == body["principal"]["principal_id"]
    )


def test_admin_can_create_and_reassign_password_users_via_api(tmp_path) -> None:
    store = _store(tmp_path)
    _, admin_token = store.login(
        login_name="admin",
        domain_id="eldercare",
        password="Admin@123",
    )
    app = FastAPI()
    app.include_router(create_principal_router(lambda: store))
    client = TestClient(app)

    created = client.post(
        "/api/v2/principals/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "display_name": "测试普通用户",
            "login_name": "test.user",
            "password": "User@123456",
            "domain_id": "eldercare",
            "platform_role": "viewer",
            "target_id": "elderly-001",
        },
    )
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["principal"]["roles"] == ["viewer"]
    assert created_body["principal"]["login_name"] == "test.user"

    users = client.get(
        "/api/v2/principals/users?domain_id=eldercare",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert users.status_code == 200
    payload = users.json()["users"]
    assert any(item["login_name"] == "admin" for item in payload)
    created_principal = next(
        item
        for item in payload
        if item["login_name"] == "test.user"
    )
    assert created_principal["role_labels"] == ["普通用户"]

    updated = client.put(
        f"/api/v2/principals/users/{created_principal['principal_id']}/role",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "domain_id": "eldercare",
            "platform_role": "admin",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["principal"]["roles"] == ["admin"]


class _VillaGateway:
    async def list_devices(self):
        return [{
            "id": "smoke-sensor-001",
            "title": "Smoke Sensor",
            "location": "hall",
            "actions": [],
        }]

    async def query_state(self, device_id):
        assert device_id == "smoke-sensor-001"
        return {"smokeLevel": 0, "alarm": False}


def test_registered_principal_must_authenticate_for_domain_api(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    principal, token = store.register(
        display_name="安防观察员",
        pin="2468",
        domain_id="smart_home",
        role_template_id="villa_security_observer",
        target_id="villa-demo",
    )
    profiles = DeploymentProfileRegistry.load()
    registry = DomainPluginRegistry([
        SmartHomeDomainPlugin(
            get_gateway=lambda: _VillaGateway(),
            get_profile=lambda: profiles.require("villa"),
            principals=store,
        ),
    ])
    app = FastAPI()
    app.include_router(create_domain_router(
        lambda: registry,
        lambda: store,
    ))
    client = TestClient(app)
    path = "/api/v2/domains/smart_home/subjects"

    anonymous = client.get(
        path,
        params={"principal_id": principal.principal_id},
    )
    authenticated = client.get(
        path,
        params={"principal_id": principal.principal_id},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert anonymous.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.json()["subjects"][0]["subject_id"] == "villa-demo"
