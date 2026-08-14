# Phase B: Combined Vehicle and Controller Handoff

## Implemented foundation

The capture latch starts a deterministic, exclusive-owner handoff protocol:

1. `readiness` confirms state, covariance, clock, phase, and actuator-health
   predicates.
2. `quiet_period` requires commands to remain below the configured threshold.
3. `transfer_pending` checks shadow-command continuity and waits for an explicit
   acknowledgement.
4. `acknowledged` records acceptance while Orion still owns authority.
5. `active` atomically assigns the configured docked-stack controller.

Readiness loss, acknowledgement timeout, or a shadow-command mismatch enters
`rollback`. The authority token remains with Orion until the active transition,
so the protocol never creates duplicate or absent authority.

Blue Moon's configured active owner is Orion. Starship's configured active owner
is the target vehicle. Both still execute the same protocol so readiness and
transition evidence remain comparable.

## Telemetry contract

Schema 2.0 adds handoff state, controller authority, command source, desired
owner, readiness, transition reason, and shadow-command delta to truth telemetry.
Every protocol transition is also a `handoff_state` mission event.

## Exchange packet and validation

The versioned exchange packet now carries sequence and source time, frame and
mission phase, relative position/velocity, attitude/angular rate, a 12-by-12
covariance matrix, and actuator-health fraction. Validation requires finite
state, a unit quaternion, symmetric positive-semidefinite covariance, bounded
data age, exact frame and phase identity, and healthy actuators. No failed field
is silently transformed, clipped, or forward-filled.

Deterministic scenario faults cover stale data, frame mismatch, lost
acknowledgement, and unhealthy actuators. Each produces rollback while Orion
retains authority; the docked mission then terminates safely rather than waiting
forever in soft capture.

Schema 2.1 records packet version, sequence, age, frame, individual validation
predicates, and the exchange rejection reason.

## Shadow control and authority invariants

Orion and the target now independently compute bounded six-axis stack-damping
wrenches from the exchanged combined velocity, angular rate, mass, and inertia.
Torque differences are converted to equivalent force using the vehicle lever
scale before comparison. Transfer is rejected if either the candidate-to-
candidate disagreement or zero-to-active activation step exceeds the configured
continuity limit.

An external authority observation checks that exactly one controller claims the
stack. Injected duplicate- and lost-authority observations are visible for the
detection sample, force rollback, and recover to Orion-only authority on the
next sample. A shadow-command mismatch fault exercises the same rollback path.
Schema 2.2 records both candidate wrenches, comparison metrics, observed owner
claims, and the authority invariant.

## Sustained selected-owner control

After activation, the mission enters a timed `docked_stack_control` phase. The
selected owner's shadow wrench becomes the commanded stack wrench, is allocated
through that vehicle's own 24-jet layout, and acts on the combined mass and full
inertia. The achieved torque includes both allocator torque and the force moment
arm from the active vehicle center to the stack COM. Stack velocity and angular
rate evolve throughout the dwell, and jet on-time is charged separately to
chaser or target propellant.

The 60-second reference dwell is long enough for both placeholder actuator
scales to accumulate and fire valid minimum-duration pulses. An injected active-
owner failure occurs after activation and several target-control samples, then
atomically rolls authority back to Orion while the stack-control phase continues.

Schema 2.3 records the selected stack controller, stack-control activation,
combined-COM achieved torque, owner-specific propellant, and active-failure
evidence.

## Phase B closure

The Phase B exit criteria are satisfied: nominal command discontinuities remain
inside their configured limit, invalid or stale transfer data rolls back,
external duplicate/lost authority is detected and recovered, exactly one
internal owner controls every non-fault state, and the two missions demonstrate
their configured post-docking owners under sustained stack control.
