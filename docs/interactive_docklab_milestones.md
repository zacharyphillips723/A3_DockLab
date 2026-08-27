# Interactive A3 DockLab Roadmap

## Product direction

A3 DockLab will evolve from a server-driven mission replay into an interactive
rendezvous and docking laboratory. A human operator, the existing autopilot, or
a model policy will drive the same simulation-session interface. Historical
replay remains available for audit, comparison, and analysis, but it is no
longer the primary interaction model.

The physics engine and safety monitor remain authoritative. Human and model
drivers submit intent; the safety layer may accept, limit, substitute, or reject
commands. Every decision must be observable and reproducible.

## Architectural target

```text
Human controls ─┐
Autopilot ──────┼─> policy/command contract ─> safety arbiter ─> simulation session
Model policy ───┘                                         │             │
                                                          │             ├─> live state
                                                          │             ├─> checkpoints
                                                          │             └─> telemetry/events
                                                          └─> command decisions

Live state ─> interactive UI
Session state/commands/reviews ─> Lakebase
Telemetry/checkpoints/completed runs ─> Delta Lake
Policies/evaluations/ensembles ─> MLflow and Databricks Jobs
```

## Milestone 1 — Step-driven simulation core

### Goal

Turn the monolithic controlled mission into a persistent deterministic session
that can advance one integration step at a time without breaking batch users.

### Scope

- Add a `SimulationSession` lifecycle: create/reset, pause/resume, step,
  bounded advance, completion, and result materialization.
- Emit a state frame and event delta after every integration step.
- Add immutable, serializable checkpoints and deterministic restore.
- Preserve `run_controlled(config)` as a compatibility wrapper over the session.
- Define the first command envelope and driver identity contract. Milestone 1
  initially uses the existing autopilot command path; external command
  arbitration is enabled in Milestone 2.
- Expose time, phase, translational state, attitude, fuel, docking metrics,
  safety margins, active authority, and new events in each frame.

### Acceptance gate

- Batch and stepped runs are frame-for-frame and event-for-event equivalent.
- Pause does not advance simulation time.
- A single step advances exactly one configured integration interval.
- Reset reproduces the initial frame.
- Restoring a checkpoint and advancing produces the same subsequent frames.
- Existing simulation, telemetry, Monte Carlo, and replay tests remain green.

## Milestone 2 — Command and safety-arbitration contract

### Goal

Allow external drivers to influence the mission without bypassing deterministic
safety behavior.

### Scope

- Define versioned observations, control intents, accepted commands, and command
  decisions.
- Support autopilot, manual translational/rotational intent, hold, approach,
  retreat, capture request, abort, and controller handoff.
- Apply rate, thrust, torque, fuel, phase, corridor, keep-out, closing-rate, and
  docking-alignment constraints in a single safety arbiter.
- Record requested and executed commands plus rejection/limiting reasons.
- Add deterministic fault injection during a live session.
- Add watchdog behavior for stale or missing driver commands.

### Acceptance gate

- No driver can bypass the safety arbiter.
- Invalid, stale, or unauthorized commands fail closed and are auditable.
- The autopilot adapter still reproduces the Milestone 1 reference mission.
- Manual commands demonstrably alter the trajectory within configured limits.

## Milestone 3 — Interactive human-in-the-loop application

### Goal

Replace replay-first interaction with a live cockpit and simulation laboratory.

### Scope

- Add session creation, reset, pause, single-step, play rate, and termination.
- Stream state to the browser over a low-latency channel.
- Add translation and attitude controls, hold/approach/retreat/abort actions,
  controller selection, and fault injection.
- Keep the synchronized 3D twin, safety geometry, event timeline, health plots,
  and KPIs live while commands are applied.
- Show requested versus executed control and explicit safety interventions.
- Retain historical replay as a separate mode.
- Support reconnecting to an active session from its latest checkpoint.

### Acceptance gate

- Controls feel continuous at the selected display rate and do not require a
  full figure rebuild.
- Two browser clients cannot silently take control of the same session.
- Disconnect and reconnect do not corrupt or fork session state.
- A complete human-driven run can be replayed exactly from its command log.

## Milestone 4 — Model-driver interface

**Status: complete.** See `docs/milestone_4_policy_interface.md` for the shipped
runtime, adapters, provenance, and acceptance evidence.

### Goal

Let control models drive the same guarded session API used by humans and the
reference autopilot.

### Scope

- Add a policy adapter with versioned observation/action spaces.
- Begin with the current autopilot as the reference adapter.
- Support deterministic local policies, MPC, and MLflow-registered policies.
- Keep high-level mission-agent decisions separate from control-rate commands.
- Add policy latency budgets, timeouts, fallback, health, and provenance.
- Add shadow mode so candidate policies can be evaluated without authority.
- Compare requested model actions with safety-approved and executed commands.

### Acceptance gate

- Policy artifacts, configuration, code revision, and observation/action schema
  are recorded for every run.
- Timeouts and model errors transition safely to hold or reference autopilot.
- Shadow and active results are reproducible from stored inputs.
- A model never receives unapproved direct access to actuator execution.

## Milestone 5 — Databricks session control plane

**Status:** Complete. Durable sessions are bound to the live runtime with owner
authorization, lease renewal, optimistic concurrency, idempotent commands, and
tested cross-instance reconstruction. Terminal sessions stage one portable
contract in a managed Volume, enqueue a Databricks Job, publish attributed
telemetry/audit tables to Delta, and record policy lineage in MLflow.

### Goal

Operate interactive sessions reliably in a Databricks workspace while keeping
the physics core portable and locally testable.

### Scope

- Persist session ownership, status, commands, annotations, reviews, and saved
  views in Lakebase.
- Write telemetry, decisions, checkpoints, and completed-run manifests to Delta.
- Use MLflow for policy registration, evaluation, and lineage.
- Use Databricks Jobs for ensembles, risk analysis, offline evaluation, and
  post-run materialization rather than the live control loop.
- Add authentication, per-session authorization, idempotency, leases, and
  optimistic concurrency.
- Define recovery behavior for App restart, scale-out, and lost clients.

### Acceptance gate

- Active-session state survives an App restart from a durable checkpoint.
- Only the lease holder can issue control commands.
- Local and Databricks-backed sessions share the same core contracts.
- Completed sessions are queryable through Lakehouse tables and attributable to
  their operator or policy.

## Milestone 6 — Risk, comparison, and review workspace

**Status:** Complete. Risk, comparison, saved reproducible review, pinned
annotation, disposition history, audit export, and deep-link workflows are
implemented against local or Databricks-backed storage contracts.

### Goal

Turn live experiments into an operational digital-twin analysis workflow.

### Scope

- Add Replay, Live Lab, Risk, Compare, and Review application areas.
- Surface Monte Carlo success/abort rates, miss distance, contact conditions,
  convergence, and scenario sensitivity.
- Compare trajectories, KPIs, command histories, safety interventions, and model
  versions across sessions.
- Add pinned annotations, saved views, approval/rejection, and review history.
- Add shareable session/time links and snapshot export.

### Acceptance gate

- Reviewers can reproduce any displayed result from stored artifacts.
- Comparison handles mismatched durations and schemas explicitly.
- Approval state and annotations remain attributable and immutable in history.

## Milestone 7 — Operational hardening

**Status:** In progress jointly with Milestone 6. Resource budgets, schema
compatibility, and append-only audit contracts are embedded in workspace
features from their first implementation slice.

### Goal

Make the lab dependable for demonstrations, collaborative testing, and extended
experiments.

### Scope

- Load, soak, recovery, latency, and multi-session tests.
- Observability for simulation rate, policy latency, dropped frames, safety
  interventions, storage lag, and reconnects.
- Schema migration and backward-compatibility policy.
- Resource quotas, session expiration, cleanup, and cost controls.
- Threat modeling, dependency scanning, access review, and audit exports.
- Deployment runbooks, acceptance tests, and rollback procedures.

### Acceptance gate

- Published service-level targets pass under expected concurrent load.
- Recovery and rollback exercises succeed without losing accepted commands.
- Security and operational checklists are complete for the target workspace.

## Cross-cutting rules

- The simulation core must remain usable without Databricks.
- Determinism is required whenever the same configuration, checkpoint, command
  log, and policy revision are supplied.
- Safety decisions and command provenance are first-class telemetry.
- UI display sampling must not change physics integration.
- Models propose intent; deterministic software owns actuator limits and safety.
- Every milestone ships behind an acceptance gate before the next becomes a
  production dependency.
