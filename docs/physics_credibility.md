# Forward Phase A: Physics Credibility

This phase establishes a nonlinear reference against which A3 DockLab's CW
model can be judged. It does not claim that point-mass two-body dynamics are a
complete truth model; it makes the current linearization error measurable.

## Implemented slice

- Numerical integration of the CW differential equations.
- ECI-to-LVLH and LVLH-to-ECI position/velocity transforms.
- Adaptive DOP853 inertial two-body propagation.
- Paired target/chaser propagation and reconstruction of nonlinear relative
  motion in the target LVLH frame.
- Separation-duration validity sweeps and machine-readable comparison reports.
- Circular-orbit conservation, frame round-trip, numerical agreement, solver
  convergence, and validity-trend tests.
- Scalar-first quaternion kinematics with an explicit body-to-LVLH convention.
- Torque-driven principal-axis rigid-body propagation with quaternion
  renormalization and torque-free energy/angular-momentum checks.
- Docking-port body transforms for both reference vehicles, including separate
  normal alignment, clocking, lateral-offset, and axial-separation metrics.
- Shortest-rotation quaternion-error PD attitude control with per-axis torque
  saturation.
- Chaser and target attitude propagation in controlled rendezvous, with attitude
  state, angular rate, commanded torque, and port-alignment telemetry.
- Port-relative terminal capture guards and deterministic retreat decisions for
  angular, clocking, and lateral misalignment.
- A bounded nonnegative six-axis allocator for a documented 24-jet symmetric
  placeholder layout, with per-jet health factors and duty commands.
- Shared translation/attitude wrench allocation, pulse accumulation for minimum
  impulse duration, allocation residuals, saturation state, individual jet
  on-time propellant accounting, and failed-jet exclusion tests.
- Combined docked-stack center of mass and full inertia tensor using rotated
  component inertias and the parallel-axis theorem.
- Post-capture mass-property truth telemetry and reproducible stack reports with
  force application-point coupling responses.
- Qualitative normal spring-damper and regularized tangential-friction contact
  forces with explicit non-structural-analysis limitations.
- Event-based perfectly inelastic capture latch with conserved total linear and
  angular momentum, reported kinetic-energy dissipation, combined velocity, and
  combined angular rate.

Generate a report with:

```bash
a3docklab physics-report configs/scenarios/blue_moon_side.yaml \
  --output reports/physics/blue_moon
```

The report contains a trajectory comparison, a validity-envelope grid, and a
summary. CW remains appropriate only inside a declared error budget for the
chosen separation, duration, and operational question.

## Remaining Phase A work

- Optional J2 and drag sensitivity.
- Replacement of placeholder properties and actuator geometry when defensible
  public data becomes available.

## Attitude and docking-frame conventions

Attitude quaternions are ordered `wxyz` and rotate vectors from a vehicle body
frame into target LVLH. Angular rates and torques are expressed in vehicle body
axes. Every docking port defines a body-frame position, outward normal, and
clocking-up vector. A nominal mate has coincident port origins, opposing outward
normals, and aligned up vectors. This makes angular misalignment and clocking
error independent observables instead of combining them into one ambiguous
Euler-angle quantity.

The scenario port locations remain explicit engineering assumptions. Until the
combined center-of-mass model lands, the controlled simulator's relative
translation is defined between docking-port origins; body-frame port offsets are
reserved for the later center-of-mass-to-port transform. Capture now requires
axial distance, lateral offset, normal alignment, and clocking to satisfy the
configured envelope. The reference lateral controller gain was rebaselined when
this guard exposed that the previous nominal trajectories violated their own
lateral capture limits.

The 24-jet cruciform layout is deliberately symmetric and scaled from the
declared vehicle envelope. It proves allocation behavior and telemetry contracts;
it is not a claim about Orion hardware. Each body-axis direction has four jets,
and `max_translation_thrust_n` is interpreted as per-axis authority. Commands
below the minimum valve-on duration accumulate until a valid pulse can be fired.
Failed jets have zero authority and cannot receive a command; unachievable force
or torque remains visible as an allocation residual.

Generate a stack report with:

```bash
a3docklab stack-report configs/scenarios/blue_moon_side.yaml \
  --output reports/stack/blue_moon
```

The report provides total mass, combined COM, the complete symmetric inertia
tensor, principal moments/axes, vehicle and interface locations, and the angular
response to unit forces applied at the chaser center. For the current Blue Moon
assumptions, the stack COM is 4.268 m from Orion's center; a transverse 1 N Orion
force therefore induces 4.268 N m of stack torque. This quantifies side-dock
coupling without claiming proprietary mass properties.

## Phase A closure

The core Physics Credibility gate is complete. Capture is modeled as a discrete,
perfectly inelastic latch after the port-relative envelope is satisfied. The
projection preserves total linear and angular momentum to numerical precision
and reports the kinetic energy dissipated by the idealized latch. A separate
spring-damper/friction function supports qualitative transient studies but is
not used to claim docking loads, structural response, latch loads, or hardware
qualification.

J2 and drag remain optional sensitivity extensions because they do not block the
short-duration terminal rendezvous, attitude, allocation, capture, and combined-
stack questions addressed by the current reference scenarios.
