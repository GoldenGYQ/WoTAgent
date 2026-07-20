"""Experiment-grade trusted principal and domain-role directory."""

from __future__ import annotations

from collections.abc import Iterable

from .models import (
    DomainRoleBinding,
    PrincipalProfile,
    PrincipalRelationship,
    TaskPrincipal,
)


class PrincipalNotAssigned(LookupError):
    pass


class PrincipalDirectory:
    def __init__(self, profiles: Iterable[PrincipalProfile]) -> None:
        self._profiles = {
            profile.principal_id: profile
            for profile in profiles
        }

    def get_profile(self, principal_id: str) -> PrincipalProfile | None:
        return self._profiles.get(principal_id)

    def login_mode(self, principal_id: str) -> str:
        profile = self.get_profile(principal_id)
        return profile.login_mode if profile else "disabled"

    def resolve(
        self,
        principal_id: str,
        domain_id: str,
        *,
        fallback_role: str | None = None,
    ) -> TaskPrincipal:
        profile = self._profiles.get(principal_id)
        if profile is None:
            if fallback_role is None:
                raise PrincipalNotAssigned(
                    f"principal '{principal_id}' is not registered"
                )
            return TaskPrincipal(
                principal_id=principal_id,
                display_name=principal_id,
                domain_id=domain_id,
                roles=[fallback_role],
            )
        binding = next(
            (
                item
                for item in profile.bindings
                if item.domain_id == domain_id
            ),
            None,
        )
        if binding is None:
            raise PrincipalNotAssigned(
                f"principal '{principal_id}' is not assigned to "
                f"domain '{domain_id}'"
            )
        return TaskPrincipal(
            principal_id=profile.principal_id,
            principal_type=profile.principal_type,
            display_name=profile.display_name,
            domain_id=domain_id,
            roles=[binding.platform_role],
            domain_roles=binding.roles,
            role_labels=binding.role_labels,
            granted_capabilities=binding.capabilities,
            resource_ids=binding.resource_ids,
            relationships=binding.relationships,
        )

    def list_contexts(
        self,
        domain_id: str,
        *,
        login_only: bool = False,
    ) -> list[TaskPrincipal]:
        contexts: list[TaskPrincipal] = []
        for profile in self._profiles.values():
            if login_only and not profile.login_enabled:
                continue
            try:
                contexts.append(self.resolve(profile.principal_id, domain_id))
            except PrincipalNotAssigned:
                continue
        return contexts


DEMO_PRINCIPALS = [
    PrincipalProfile(
        principal_id="ADMIN-1",
        display_name="系统管理员",
        login_enabled=True,
        login_mode="password",
        login_name="admin",
        bootstrap_secret="Admin@123",
        bindings=[
            DomainRoleBinding(
                domain_id="eldercare",
                platform_role="admin",
                roles=["system_admin"],
                role_labels=["系统管理员"],
                capabilities=["*"],
                resource_ids=["*"],
            ),
            DomainRoleBinding(
                domain_id="smart_home",
                platform_role="admin",
                roles=["system_admin"],
                role_labels=["系统管理员"],
                capabilities=["*"],
                resource_ids=["*"],
            ),
        ],
    ),
    PrincipalProfile(
        principal_id="USER-1",
        display_name="张建国用户",
        login_enabled=True,
        login_mode="password",
        login_name="user01",
        bootstrap_secret="User@123456",
        bindings=[
            DomainRoleBinding(
                domain_id="eldercare",
                platform_role="viewer",
                roles=["resident_user"],
                role_labels=["普通用户"],
                capabilities=[
                    "domain.read",
                    "resident.context.read",
                    "health.status.read",
                    "health.summary.read",
                    "health.vitals.read",
                ],
                resource_ids=["resident:elderly-001"],
                relationships=[PrincipalRelationship(
                    relation="self",
                    target_type="resident",
                    target_id="elderly-001",
                    target_label="张爷爷",
                )],
            ),
            DomainRoleBinding(
                domain_id="smart_home",
                platform_role="viewer",
                roles=["standard_user"],
                role_labels=["普通用户"],
                capabilities=[
                    "domain.read",
                    "device.read",
                ],
                resource_ids=["site:villa-demo"],
                relationships=[PrincipalRelationship(
                    relation="member_of",
                    target_type="site",
                    target_id="villa-demo",
                    target_label="云栖别墅",
                )],
            ),
        ],
    ),
    PrincipalProfile(
        principal_id="JH-1",
        display_name="王敏",
        login_enabled=True,
        login_mode="one_click",
        bindings=[
            DomainRoleBinding(
                domain_id="eldercare",
                platform_role="operator",
                roles=["caregiver"],
                role_labels=["护理员"],
                capabilities=[
                    "domain.read",
                    "device.read",
                    "health.status.read",
                    "health.summary.read",
                    "health.vitals.read",
                    "health.trend.read",
                    "health.data_quality.read",
                    "resident.context.read",
                    "care.coordinate",
                    "environment.control",
                    "scenario.execute",
                    "event.acknowledge",
                ],
                resource_ids=["resident:elderly-001"],
                relationships=[PrincipalRelationship(
                    relation="caregiver_of",
                    target_type="resident",
                    target_id="elderly-001",
                    target_label="张爷爷",
                )],
            ),
            DomainRoleBinding(
                domain_id="smart_home",
                platform_role="operator",
                roles=["villa_owner"],
                role_labels=["别墅业主"],
                capabilities=[
                    "domain.read",
                    "device.read",
                    "device.control",
                    "environment.use",
                    "security.use",
                    "security.observe",
                    "energy.use",
                    "housekeeping.use",
                    "device_ops.use",
                    "scenario.execute",
                    "event.acknowledge",
                ],
                resource_ids=["site:villa-demo"],
                relationships=[PrincipalRelationship(
                    relation="owner_of",
                    target_type="site",
                    target_id="villa-demo",
                    target_label="云栖别墅",
                )],
            ),
        ],
    ),
    PrincipalProfile(
        principal_id="JH-2",
        display_name="赵强",
        login_enabled=True,
        login_mode="one_click",
        bindings=[
            DomainRoleBinding(
                domain_id="eldercare",
                platform_role="viewer",
                roles=["family_observer"],
                role_labels=["家属观察者"],
                capabilities=[
                    "domain.read",
                    "device.read",
                    "health.status.read",
                    "health.summary.read",
                    "health.vitals.read",
                    "health.trend.read",
                    "health.data_quality.read",
                    "resident.context.read",
                ],
                resource_ids=["resident:elderly-002"],
                relationships=[PrincipalRelationship(
                    relation="family_of",
                    target_type="resident",
                    target_id="elderly-002",
                    target_label="李奶奶",
                )],
            ),
            DomainRoleBinding(
                domain_id="smart_home",
                platform_role="viewer",
                roles=["security_staff"],
                role_labels=["安保人员"],
                capabilities=[
                    "domain.read",
                    "device.read",
                    "security.observe",
                ],
                resource_ids=["site:villa-demo"],
                relationships=[PrincipalRelationship(
                    relation="assigned_to",
                    target_type="site",
                    target_id="villa-demo",
                    target_label="云栖别墅",
                )],
            ),
        ],
    ),
    PrincipalProfile(
        principal_id="JH-3",
        display_name="张建国",
        login_enabled=True,
        login_mode="one_click",
        bindings=[DomainRoleBinding(
            domain_id="eldercare",
            platform_role="viewer",
            roles=["resident"],
            role_labels=["居民本人"],
            capabilities=[
                "domain.read",
                "health.status.read",
                "health.summary.read",
                "health.vitals.read",
            ],
            resource_ids=["resident:elderly-001"],
            relationships=[PrincipalRelationship(
                relation="self",
                target_type="resident",
                target_id="elderly-001",
                target_label="张爷爷",
            )],
        )],
    ),
    PrincipalProfile(
        principal_id="JH-4",
        display_name="刘倩",
        login_enabled=True,
        login_mode="one_click",
        bindings=[DomainRoleBinding(
            domain_id="eldercare",
            platform_role="operator",
            roles=["duty_caregiver"],
            role_labels=["值班护理员"],
            capabilities=[
                "domain.read",
                "device.read",
                "health.status.read",
                "health.summary.read",
                "health.vitals.read",
                "health.trend.read",
                "health.data_quality.read",
                "resident.context.read",
                "care.coordinate",
                "environment.control",
                "scenario.execute",
                "event.acknowledge",
            ],
            resource_ids=[
                "resident:elderly-001",
                "resident:elderly-002",
            ],
            relationships=[
                PrincipalRelationship(
                    relation="duty_caregiver_of",
                    target_type="resident",
                    target_id="elderly-001",
                    target_label="张爷爷",
                ),
                PrincipalRelationship(
                    relation="duty_caregiver_of",
                    target_type="resident",
                    target_id="elderly-002",
                    target_label="李奶奶",
                ),
            ],
        )],
    ),
    PrincipalProfile(
        principal_id="VH-1",
        display_name="陈悦",
        login_enabled=True,
        login_mode="one_click",
        bindings=[DomainRoleBinding(
            domain_id="smart_home",
            platform_role="operator",
            roles=["family_member"],
            role_labels=["家庭成员"],
            capabilities=[
                "domain.read",
                "device.read",
                "device.control",
                "environment.use",
            ],
            resource_ids=["site:villa-demo"],
            relationships=[PrincipalRelationship(
                relation="member_of",
                target_type="site",
                target_id="villa-demo",
                target_label="云栖别墅",
            )],
        )],
    ),
    PrincipalProfile(
        principal_id="VH-2",
        display_name="李芳",
        login_enabled=True,
        login_mode="one_click",
        bindings=[DomainRoleBinding(
            domain_id="smart_home",
            platform_role="operator",
            roles=["housekeeper"],
            role_labels=["家政人员"],
            capabilities=[
                "domain.read",
                "device.read",
                "device.control",
                "housekeeping.use",
            ],
            resource_ids=["site:villa-demo"],
            relationships=[PrincipalRelationship(
                relation="assigned_to",
                target_type="site",
                target_id="villa-demo",
                target_label="云栖别墅",
            )],
        )],
    ),
    PrincipalProfile(
        principal_id="elderly-002",
        display_name="李奶奶",
        bindings=[DomainRoleBinding(
            domain_id="eldercare",
            platform_role="viewer",
            roles=["resident"],
            role_labels=["居民本人"],
            capabilities=[
                "domain.read",
                "health.status.read",
                "health.summary.read",
                "health.vitals.read",
            ],
            resource_ids=["resident:elderly-002"],
            relationships=[PrincipalRelationship(
                relation="self",
                target_type="resident",
                target_id="elderly-002",
                target_label="李奶奶",
            )],
        )],
    ),
    PrincipalProfile(
        principal_id="system:eldercare-monitor",
        principal_type="service",
        display_name="养老规则监视服务",
        bindings=[DomainRoleBinding(
            domain_id="eldercare",
            platform_role="operator",
            roles=["monitor_service"],
            role_labels=["确定性监视服务"],
            capabilities=["*"],
            resource_ids=[
                "resident:elderly-001",
                "resident:elderly-002",
            ],
            relationships=[
                PrincipalRelationship(
                    relation="monitors",
                    target_type="resident",
                    target_id="elderly-001",
                    target_label="张爷爷",
                ),
                PrincipalRelationship(
                    relation="monitors",
                    target_type="resident",
                    target_id="elderly-002",
                    target_label="李奶奶",
                ),
            ],
        )],
    ),
    PrincipalProfile(
        principal_id="local-user",
        display_name="本地管理员",
        bindings=[
            DomainRoleBinding(
                domain_id=domain_id,
                platform_role="operator",
                roles=["local_operator"],
                role_labels=["本地实验操作员"],
                capabilities=["*"],
                resource_ids=["*"],
            )
            for domain_id in ("eldercare", "smart_home")
        ],
    ),
]


DEFAULT_PRINCIPAL_DIRECTORY = PrincipalDirectory(DEMO_PRINCIPALS)


__all__ = [
    "DEFAULT_PRINCIPAL_DIRECTORY",
    "DEMO_PRINCIPALS",
    "PrincipalDirectory",
    "PrincipalNotAssigned",
]
