# Phase 2: Controlled CW Rendezvous

Phase 2 turns the analytic CW foundation into a deterministic closed-loop
rendezvous mission. It is an engineering demonstration, not flight guidance.

## Mission sequence

Both scenarios execute:

1. Initialization
2. Far-field approach
3. Hold point 1
4. Proximity operations
5. Hold point 2
6. Final approach
7. Soft capture
8. Complete

Hold transitions require both the configured dwell time and an axial speed no
greater than 0.02 m/s. This prevents a vehicle from satisfying a hold merely by
crossing its range threshold at speed.

## Control and safety

The translation controller cancels nominal CW acceleration, tracks a
range-dependent closing command, corrects lateral displacement, saturates total
force at vehicle authority, quantizes sub-minimum impulses, and accounts for
ideal propellant use.

The safety monitor is independent of the controller. Excess closing rate
commands braking. Corridor departure or unauthorized keep-out-zone entry
commands retreat. The simulator records both the trigger and completion of the
abort response.

## Run artifacts

- `telemetry.csv`: truth state, reference velocity, errors, force, propellant,
  margins, phase, and warnings.
- `events.csv`: phase transitions, abort triggers, and abort-response completion.
- `metadata.json`: configuration identity, seed, source revision, and assumption
  snapshot.

These contracts are intentionally portable. A Databricks adapter can map them
to the proposed `simulation_runs`, `telemetry_samples`, `mission_events`, and
`assumption_snapshots` Delta tables without importing platform SDKs into the
physics engine.
