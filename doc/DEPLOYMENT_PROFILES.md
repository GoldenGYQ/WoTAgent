# WoTAgent Deployment Profiles

## Purpose

WoTAgent separates three independent boundaries:

```text
Deployment Profile
  -> which site, spaces, devices and domain prompt exist

AgentSpec and MCP Scope
  -> which capability family a specialist Agent may use

RBAC and Relationship Policy
  -> what the current Principal may do to a target resource

Domain Operations
  -> how that world projects subjects, actors, resources, observations,
     deterministic rules, events and actions to the operations console
```

A Profile is not a role. Switching from `eldercare` to `villa` changes the
active IoT world; changing from `viewer` to `operator` changes permissions
inside that world.

## Shared Catalog

The source of truth is:

```text
config/deployment_profiles.json
```

Both the backend and `wot-device-simulator` load this file. The current
profiles are:

| Profile | Domain | Site | Spaces | Devices |
|---|---|---|---:|---:|
| `eldercare` | `eldercare` | `nursing-home-demo` | 2 | 8 |
| `villa` | `smart_home` | `villa-demo` | 12 | 23 |

Device membership is exclusive. The catalog validator rejects a device
assigned to multiple profiles.

Every selectable Profile must register one `DomainOperations` adapter with the
same `domain_id`. The current projections are:

| Profile | Operations center | Primary software actors |
|---|---|---|
| `eldercare` | 养老照护管控 | Butler, Health, Care, eldercare Monitor |
| `villa` | 别墅智能管控 | Butler/秘书, Security/保安, Environment, Energy, Housekeeping, DeviceOps, villa rule Monitor |

Software actors are not login users. `JH-1` and `JH-2` are authorization
Principals; Butler and specialist Agents are delegated software Principals with
their own AgentSpec/MCP capability boundaries.

The backend identity directory resolves the same human Principal differently
for each domain:

| Principal | Eldercare binding | Smart-home binding |
|---|---|---|
| `JH-1` | caregiver, `caregiver_of elderly-001` | villa owner, `owner_of site:villa-demo` |
| `JH-2` | family observer, `family_of elderly-002` | security staff, assigned to `site:villa-demo` |

These human roles must not be used as specialist Agent identities. Specialist
names describe capability categories such as health analysis, care
coordination, security response and environment control.

## Runtime Flow

```text
Frontend selects Profile
-> PUT /api/v2/deployment/active
-> backend validates Profile
-> simulator activates the same Profile
-> frontend opens a new Conversation
-> POST /api/v2/runs carries profile_id
-> Run persists profile_id/domain_id/site_id
-> Supervisor receives compact domain context
-> device Gateway filters TD capabilities
-> MCP rejects cross-Profile device calls
```

One Conversation cannot mix Profiles. The backend returns `409` when a client
tries to reuse a Conversation after switching worlds.

## APIs

```text
GET /api/v2/deployment/profiles
PUT /api/v2/deployment/active
GET /api/v2/principals?domain_id=<domain_id>&login_only=true
GET /api/v2/principals/{principal_id}/context?domain_id=<domain_id>
GET /api/v2/principals/role-templates?domain_id=<domain_id>
GET /api/v2/principals/registration-targets?domain_id=<domain_id>
POST /api/v2/principals/register
POST /api/v2/principals/login
GET /api/v2/principals/session?domain_id=<domain_id>

GET /api/scenes
GET /api/scenes/active
PUT /api/scenes/active
```

The first pair belongs to WoTAgent. The second group belongs to the simulator.

## Adding A Profile

1. Add the profile to `config/deployment_profiles.json`.
2. Assign only existing TD device IDs.
3. Keep device membership disjoint.
4. Define spaces, enabled Agents and the domain-specific prompt.
5. Register a matching `DomainOperations` adapter. A Profile exposed to the
   frontend without one is an incomplete scene implementation.
6. Run backend, frontend and simulator regression tests.

## Current P1 Boundary

For eldercare, the current backend already applies:

```text
role capability
AND Principal-to-Resident assignment
AND effective resident ResourceScope
AND Health MCP scope validation
```

`JH-1` is assigned to `elderly-001`; `JH-2` is assigned to `elderly-002`.
These are still demo identities supplied by the client, so this is an
experiment-grade authorization model rather than production authentication.

For the villa Profile, the first relationship scope is site-level:

```text
JH-1 -> site:villa-demo
  -> domain.read, device.read, device.control,
     scenario.execute, event.acknowledge

JH-2 -> site:villa-demo
  -> domain.read, device.read
```

This is intentionally smaller than a final room/post/shift relationship model.
It proves that the same Domain Operations API can enforce different Principal
capabilities without asking the LLM to grant access.

The execution boundary is computed as:

```text
Principal capability and relationship scope
INTERSECT Profile devices
INTERSECT AgentSpec and MCP scope
INTERSECT TD and Policy constraints
```

The resulting `effective_scope` is issued by the backend. A Supervisor may
request a capability, but it cannot issue or widen that scope.

Registration uses the same boundary. The frontend selects a backend-owned role
template and resource target; it cannot submit raw capabilities. Seeded demo
Principals use one-click login. Persisted user-created Principals use a PIN and
must present their Bearer session to Run and Domain Operations endpoints.
