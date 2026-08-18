# Milestone 2 Command and Safety-Arbitration Contract

## Purpose

Milestone 2 establishes one command boundary for the reference autopilot, human
operators, and future model policies. Drivers never write actuator state. They
submit a versioned `ControlIntent`; the deterministic `CommandArbiter` returns a
versioned `CommandDecision`; only the executed portion of that decision enters
the existing controller and thruster allocator.

## Per-step sequence

1. The simulation constructs a `SimulationObservation` from authoritative state.
2. The selected driver submits a `ControlIntent`, or submits nothing to use the
   reference autopilot.
3. The arbiter verifies ownership, time validity, payload validity, phase gates,
   safety margins, closing rate, velocity, and torque limits.
4. The arbiter marks the request accepted, limited, substituted, or rejected.
5. Approved velocity and torque targets enter the existing control/allocation
   path. Rejected requests fail to hold; stale requests fall back to autopilot;
   unsafe closing requests substitute a retreat command.
6. Requested and executed commands, driver provenance, status, and reason are
   written into the simulation frame.

## Driver contract

Every intent identifies:

- schema version and command ID;
- driver ID and kind (`autopilot`, `human`, or `model`);
- simulation issue time and validity duration;
- mode (`autopilot`, `velocity`, `hold`, `retreat`, `abort`, or `capture`);
- optional desired LVLH velocity and body torque.

The session may be bound to one authorized driver ID. A command from any other
driver is rejected and produces zero velocity intent. This local ownership check
will be backed by a durable Lakebase lease in Milestone 5.

## Fail-closed behavior

- Future-dated commands are rejected.
- Stale commands are replaced with the reference autopilot command.
- Capture is rejected until the existing capture gate is satisfied.
- Closing commands outside the corridor or across the keep-out boundary are
  replaced by radial retreat.
- Closing rate and total velocity are limited deterministically.
- Per-axis torque is clipped to configured vehicle limits.
- An authorized abort command transitions the mission to `abort`, executes a
  retreat target, and emits an `operator_abort` event.
- Missing intent selects the reference autopilot, preserving batch behavior.

## Determinism and checkpoints

Checkpoints include the accepted session's complete intent log through the
checkpoint step. Restore replays that log through the same arbiter and validates
the resulting state before continuing. This makes human/model-controlled paths
reproducible without serializing Python generator internals.

## Telemetry fields

Each frame records command ID, driver ID/kind, requested mode, decision
status/reason, requested and executed velocity, and requested and executed
torque. These fields are the source for later UI safety-intervention displays and
Delta command-decision tables.

## Remaining work before Milestone 3

The core command boundary is ready for an application transport. Live fault
injection and explicit authority-handoff requests will be added alongside the
session service because they require session-scoped lifecycle and ownership
state, not just a one-step vehicle command. Lakebase leases and multi-client
concurrency remain Milestone 5 concerns.
