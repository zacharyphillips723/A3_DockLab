# Milestone 4 — Policy interface, first slice

This slice introduces the model-driver seam without granting a model direct
actuator authority.

## Contract

Every policy adapter exposes immutable `PolicyMetadata` containing its policy
ID and version, adapter type, and observation/action schema versions. A policy
receives the existing authoritative `SimulationObservation` and returns the
same versioned `ControlIntent` used by human operators. `PolicyDriver` sends
that intent through `CommandArbiter`; policies cannot write controller or
thruster state.

`ReferenceAutopilotPolicy` is the first adapter. It wraps the existing guidance
behavior in this contract and is deterministic from the observation time.

## Active and shadow execution

`SimulationSession` accepts an optional active policy and an optional shadow
policy. Active policy intent is used only when no external human/model intent is
present and still passes through the arbiter. A shadow policy is evaluated from
the same observation and against the same safety constraints, but its approved
decision is attached to the frame without changing the executed command,
telemetry, events, or vehicle state.

Live Lab session creation can enable the reference autopilot in shadow mode.
The UI shows policy provenance, proposed and safety-approved velocity, decision
status, and an explicit “shadow only” authority label beside the active human
decision.

## Remaining Milestone 4 slices

- policy latency budgets, timeout/failure containment, and fallback health;
- deterministic local-policy and MPC adapters;
- MLflow registry loading and full artifact/code/configuration provenance;
- high-level mission-agent decisions separated from control-rate policy calls;
- persisted active-versus-shadow evaluation summaries.
