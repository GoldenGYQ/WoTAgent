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
5. Add domain operations only when the scene needs a dedicated operations
   adapter; ordinary Agent use does not require one.
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
