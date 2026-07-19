"""Role templates derive capabilities; registration never accepts raw grants."""

from __future__ import annotations

from .models import RegistrationTarget, RoleTemplate


ROLE_TEMPLATES = [
    RoleTemplate(
        template_id="eldercare_family_observer",
        domain_id="eldercare",
        label="家属观察者",
        description="查看所关联居民的健康摘要、生命体征和告警。",
        platform_role="viewer",
        roles=["family_observer"],
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
        target_type="resident",
        relation="family_of",
    ),
    RoleTemplate(
        template_id="eldercare_caregiver",
        domain_id="eldercare",
        label="护理员",
        description="查看居民状态、协同照护并执行低风险环境调节。",
        platform_role="operator",
        roles=["caregiver"],
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
        target_type="resident",
        relation="caregiver_of",
    ),
    RoleTemplate(
        template_id="eldercare_resident",
        domain_id="eldercare",
        label="居民本人",
        description="查看与本人绑定的健康摘要和生命体征。",
        platform_role="viewer",
        roles=["resident"],
        capabilities=[
            "domain.read",
            "health.status.read",
            "health.summary.read",
            "health.vitals.read",
        ],
        target_type="resident",
        relation="self",
    ),
    RoleTemplate(
        template_id="villa_family_member",
        domain_id="smart_home",
        label="家庭成员",
        description="查看住宅设备并控制普通环境设备。",
        platform_role="operator",
        roles=["family_member"],
        capabilities=[
            "domain.read",
            "device.read",
            "device.control",
            "environment.use",
        ],
        target_type="site",
        relation="member_of",
    ),
    RoleTemplate(
        template_id="villa_security_observer",
        domain_id="smart_home",
        label="安保观察员",
        description="查看入口、门锁、烟雾和燃气等安防状态。",
        platform_role="viewer",
        roles=["security_staff"],
        capabilities=[
            "domain.read",
            "device.read",
            "security.observe",
        ],
        target_type="site",
        relation="assigned_to",
    ),
    RoleTemplate(
        template_id="villa_housekeeper",
        domain_id="smart_home",
        label="家政人员",
        description="查看住宅设备并使用授权家务设备。",
        platform_role="operator",
        roles=["housekeeper"],
        capabilities=[
            "domain.read",
            "device.read",
            "device.control",
            "housekeeping.use",
        ],
        target_type="site",
        relation="assigned_to",
    ),
]

REGISTRATION_TARGETS = [
    RegistrationTarget(
        domain_id="eldercare",
        target_type="resident",
        target_id="elderly-001",
        label="张爷爷",
    ),
    RegistrationTarget(
        domain_id="eldercare",
        target_type="resident",
        target_id="elderly-002",
        label="李奶奶",
    ),
    RegistrationTarget(
        domain_id="smart_home",
        target_type="site",
        target_id="villa-demo",
        label="云栖别墅",
    ),
]


def role_templates(domain_id: str) -> list[RoleTemplate]:
    return [
        item
        for item in ROLE_TEMPLATES
        if item.domain_id == domain_id and item.self_register
    ]


def require_role_template(
    template_id: str,
    domain_id: str,
) -> RoleTemplate:
    template = next(
        (
            item
            for item in ROLE_TEMPLATES
            if item.template_id == template_id
            and item.domain_id == domain_id
            and item.self_register
        ),
        None,
    )
    if template is None:
        raise LookupError(
            f"role template '{template_id}' is not available in "
            f"domain '{domain_id}'"
        )
    return template


def registration_targets(domain_id: str) -> list[RegistrationTarget]:
    return [
        item for item in REGISTRATION_TARGETS
        if item.domain_id == domain_id
    ]


def require_registration_target(
    domain_id: str,
    target_type: str,
    target_id: str,
) -> RegistrationTarget:
    target = next(
        (
            item
            for item in REGISTRATION_TARGETS
            if item.domain_id == domain_id
            and item.target_type == target_type
            and item.target_id == target_id
        ),
        None,
    )
    if target is None:
        raise LookupError(
            f"registration target '{target_type}:{target_id}' is not "
            f"available in domain '{domain_id}'"
        )
    return target


__all__ = [
    "REGISTRATION_TARGETS",
    "ROLE_TEMPLATES",
    "registration_targets",
    "require_registration_target",
    "require_role_template",
    "role_templates",
]
