# Phase 3: Synthetic Telemetry and Mission Replay

## Objective

Phase 3 turns completed controlled simulations into observable missions. It
generates noisy, delayed, fault-labeled telemetry and provides a Plotly/Dash
application for replaying nominal and faulty runs. The same application service
must work with local artifacts and, later, Databricks-backed adapters.

The implemented application lets an operator select a run, replay it, inspect the
vehicle state and safety margins, jump to a phase or fault event, and distinguish
truth from received telemetry without loading an ensemble into browser memory.

## Implementation status

Phase 3 is implemented. Versioned bundles contain Parquet truth, navigation,
navigation-estimate, actuation, communications, and fault-label streams plus
JSON Lines events. The replay application supports play/pause, speed control,
time scrubbing, event jumps, and run comparison. Local storage and Phase 2
compatibility are implemented; the Databricks `ReplayStore` adapter remains the
workspace-plumbing handoff described in P3.5.

## Decisions locked during preparation

1. **Preserve source timing.** Every sampled stream carries `event_time_ns` and
   `receive_time_ns`. Missing packets remain missing; storage never silently
   forward-fills them.
2. **Separate truth from observations.** The controller truth state, navigation
   estimates, received sensor data, and labels are distinct contracts. Fault
   labels are not eligible model features.
3. **Keep immutable and mutable data apart.** Lakehouse tables hold immutable run
   artifacts. Lakebase holds annotations, bookmarks, saved comparisons, and
   other application state.
4. **Use a service boundary for replay.** Dash callbacks consume a `ReplayStore`
   interface rather than reading CSV, Delta, or Lakebase directly.
5. **Retain a compatibility view.** The Phase 2 `telemetry.csv` remains available
   for transparent inspection while Parquet stream files become the preferred
   local representation.
6. **Decimate for display, retain for analysis.** The backend selects an
   appropriate point budget for plots. Raw samples remain available for a
   timestamp-centered detail view.
7. **Version every contract.** Phase 3 artifacts use `schema_version: 1.0` and
   reject unsupported major versions at load time.

The machine-readable contract is in
[`contracts/phase_3_tables.yaml`](contracts/phase_3_tables.yaml).

## Initial synthetic telemetry scope

### Streams

- Dynamics truth at 10 Hz for the first release.
- Control and actuation at 10 Hz.
- Relative navigation observations at 5 Hz.
- Navigation estimates at 10 Hz.
- Communications status at 2 Hz.
- Discrete mission and fault events at native event times.

Higher rates from the design document remain configurable future targets. Ten
hertz is sufficient for the first replay and anomaly baseline without inflating
each 50-minute mission into an unnecessarily large local artifact.

### Initial fault set

Phase 3 implements three faults end to end:

1. Navigation range drift with bias and random walk.
2. Thruster underperformance with commanded-versus-actual force residual.
3. Communications latency and packet loss with explicit receive timestamps.

Each fault has a configured onset, recovery, severity, affected channels, and
ground-truth event. Attitude and docking-alignment faults wait for the 6-DOF
model rather than inventing signals the current dynamics cannot support.

## Replay application

### Main view

- Scenario, run identity, source revision, seed, and terminal status.
- 3D LVLH trajectory with target, hold points, keep-out zone, and corridor.
- Current phase, range, closing rate, propellant use, and safety margins.
- Truth-versus-navigation state and commanded-versus-actual thrust.
- Communications age, validity, fault state, and active warnings.

### Timeline and controls

- Play, pause, step, speed, and timestamp scrubber.
- Jump to phase transition, warning, abort, fault onset, or recovery.
- Synchronized range, closing-rate, residual, thrust, and margin plots.
- Truth overlay disabled by default outside explicit analysis/replay mode.
- Nominal-versus-faulty run comparison using aligned mission phase and time.

### Backend behavior

- List and summarize runs without loading telemetry bodies.
- Load only a requested time window and columns.
- Return decimated plot data with a configurable maximum point count.
- Cache immutable run summaries and invalidate by run/configuration hash.
- Keep all storage-specific code outside Dash callbacks.

## Work packages

### P3.0 — Contract implementation

- Add Pydantic models for run, stream, sample, and event manifests.
- Add nanosecond event/receive timestamps and channel-validity fields.
- Extend `RunStorage`; introduce read-only `ReplayStore` and local adapter.
- Write Parquet streams and JSON Lines events when the storage extra is installed.
- Preserve CSV compatibility output.

Acceptance:

- Invalid schema versions and duplicate stream keys are rejected.
- A local bundle round-trips without data or timestamp loss.
- Phase 2 bundles remain readable through a compatibility loader.

### P3.1 — Multi-rate telemetry generator

- Add deterministic clocks for 10 Hz, 5 Hz, and 2 Hz streams.
- Produce noisy relative-range and relative-state observations.
- Produce a minimal navigation estimate and uncertainty columns.
- Preserve sample sequence, event time, receive time, and validity.

Acceptance:

- Fixed seeds reproduce all samples and event records.
- Stream counts match their configured rates exactly.
- Noise statistics pass bounded statistical tests.

### P3.2 — Fault injection and labels

- Implement navigation drift, thruster scale error, latency, and packet loss.
- Emit onset/recovery events and sample-level affected-channel masks.
- Add feature allowlists excluding truth and label columns.

Acceptance:

- Fault windows align with timestamps.
- Thruster underperformance changes actual force but not commanded force.
- Latency changes receive time without rewriting event time.
- Missing packets remain explicit and are never forward-filled by storage.

### P3.3 — Replay service

- Implement run catalog, summary, window query, event query, and comparison APIs.
- Add deterministic min/max decimation that retains extrema and event boundaries.
- Calculate display-ready derived values in the backend.

Acceptance:

- A 30-minute window can be queried without reading unrelated streams.
- Decimation retains phase, fault, warning, and abort boundaries.
- Local and mock-Delta adapters pass the same contract tests.

### P3.4 — Dash application

- Build run selector, mission summary, 3D view, synchronized plots, and controls.
- Add event jumps and nominal/fault comparison.
- Add loading, empty, corrupt-bundle, and unsupported-schema states.

Acceptance:

- Both nominal Phase 2 missions replay from start to completion.
- A faulty mission exposes onset, affected channels, and received-data effects.
- Browser callbacks never receive a complete multi-run ensemble.
- Core callbacks and layouts have automated smoke tests.

### P3.5 — Databricks handoff package

- Provide Delta table DDL and ingestion mapping from the local contracts.
- Document `ReplayStore` methods required from a Databricks adapter.
- Provide environment-variable configuration and no embedded credentials.
- Define Lakebase tables for annotations and saved comparisons.

Acceptance:

- Genie can implement platform adapters without changing simulation or Dash
  domain code.
- Contract tests can run against local and Databricks implementations.

## Recommended implementation order

Build P3.0 through P3.3 before the visual application. Then construct the Dash
view against the replay service and finish with the Databricks handoff package.
This makes the UI a consumer of stable behavior rather than the place where data
semantics are invented.

## Explicitly deferred

- Isolation Forest, autoencoder, and temporal-network benchmarking.
- MLflow model promotion workflows.
- Distributed Monte Carlo orchestration.
- Six-degree-of-freedom attitude and contact visualization.
- Live spacecraft or operational command interfaces.

Those capabilities consume Phase 3 telemetry but do not belong in the first
replay milestone.
