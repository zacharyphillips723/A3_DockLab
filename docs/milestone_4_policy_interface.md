# Milestone 4 — Model-driver interface

Milestone 4 lets deterministic local, MPC-style, high-level agent, and
MLflow-loaded policies drive the same guarded session contract used by humans.
No policy receives actuator access.

## Runtime and safety

Every adapter exposes immutable `PolicyMetadata`: policy and adapter versions,
observation/action schema versions, artifact URI, configuration digest, and
code revision. `PolicyDriver` runs each proposal within a configurable latency
budget. Timeout, exception, or malformed output produces a health result and a
deterministic hold or reference-autopilot fallback. The fallback is itself a
`ControlIntent` and must pass through `CommandArbiter`.

Each frame and completed telemetry row records policy provenance, latency,
health, and fallback status. Fallbacks also emit `policy_fallback` events. The
session API retains ordered active and shadow evaluation records.

## Adapters

- `ReferenceAutopilotPolicy` wraps the reference guidance path.
- `StationKeepingPolicy` provides a deterministic local hold policy.
- `CorridorMpcPolicy` provides a deterministic receding-horizon-style approach
  adapter whose output remains bounded by the arbiter.
- `MissionAgentPolicy` converts high-level approach, hold, retreat, and abort
  directives into control intents; mission reasoning remains separate from the
  control-rate boundary.
- `MlflowPyfuncPolicy` loads a registered pyfunc model lazily in an MLflow or
  Databricks runtime and records its model URI, version, and code revision.

## Active and shadow operation

Live Lab can select a human or built-in policy as the active controller and can
evaluate another policy in shadow. A human command is authoritative only in
human mode. Shadow decisions carry the same safety evaluation and provenance
but never alter the active command, vehicle state, or physics integration.

The UI displays active policy health, measured latency versus budget, fallback,
artifact/revision provenance, and proposed-versus-approved shadow action with
an explicit no-authority label.

## API additions

- `GET /api/simulations/policies`
- policy/runtime fields on `POST /api/simulations`
- `GET /api/simulations/{session_id}/policy-evaluations`

MLflow loading remains optional locally. Databricks model registry credentials
and durable evaluation persistence belong to the Milestone 5 control plane.

## Acceptance evidence

- reference policy preserves the reference physical trajectory;
- deterministic MPC and mission-agent outputs are reproducible;
- timeouts, exceptions, and invalid output fail closed and are auditable;
- active and shadow intent always passes through `CommandArbiter`;
- shadow evaluation does not change the executed vehicle state;
- artifact, configuration, code, and schema provenance is recorded;
- browser validation covers human, active-policy, and shadow-policy operation.
