# A3 DockLab

A3 DockLab is an open-source Artemis III rendezvous-and-docking digital twin. It models Orion as the chaser spacecraft for two low-Earth-orbit demonstrations:

1. Side docking with a Blue Moon Mark 2-derived test lander, followed by Orion-controlled docked-stack operations.
2. Nose-to-nose docking with a Starship Version 3-derived test article, followed by Starship-controlled docked-stack operations.

The repository is intentionally an engineering research simulator, not flight software and not a representation of proprietary NASA, Blue Origin, SpaceX, Lockheed Martin, or ESA data.

## What is included

- Clohessy-Wiltshire relative-motion propagation
- Phase-controlled rendezvous with configurable hold points
- Range-dependent guidance and bounded translation control
- Keep-out, approach-corridor, closing-rate, and abort monitoring
- Propellant accounting and discrete mission-event logging
- Validated LVLH/ECI transforms and numerical two-body propagation
- CW analytic/numerical/nonlinear comparison and validity-envelope reporting
- Quaternion rigid-body attitude propagation and docking-port alignment metrics
- Port-relative capture guards and deterministic alignment-abort behavior
- Bounded 24-jet force/torque allocation with pulse and failure accounting
- Combined-stack COM, full inertia tensor, and off-center force coupling reports
- Momentum-conserving capture latch and qualitative compliant-contact model
- Exclusive controller-authority handoff with acknowledgement and rollback
- Validated state/covariance/clock/frame/phase/health handoff exchange
- Dual shadow-controller continuity and exactly-one-authority monitoring
- Sustained owner-specific docked-stack control and active-failure recovery
- CW extended Kalman filtering with covariance and innovation diagnostics
- Reproducible correlated Monte Carlo ensembles and mission-risk summaries
- Configurable vehicle, docking, sensor, thruster, and safety assumptions
- Phase-based rendezvous state machine
- Synthetic telemetry and fault injection schemas
- Anomaly-detection experiment plan
- Dashboard and 3D visualization architecture
- Verification and Monte Carlo strategy

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
pytest
python -m a3docklab.cli simulate configs/scenarios/blue_moon_side.yaml --output runs
python -m a3docklab.cli telemetry configs/scenarios/blue_moon_side.yaml --output bundles
python -m a3docklab.cli replay bundles
python -m a3docklab.cli physics-report configs/scenarios/blue_moon_side.yaml --output reports/physics
python -m a3docklab.cli stack-report configs/scenarios/blue_moon_side.yaml --output reports/stack
python -m a3docklab.cli monte-carlo configs/scenarios/blue_moon_side.yaml --ensemble-config configs/monte_carlo/default.yaml --output ensembles
```

Install optional components as needed:

```bash
pip install -e ".[ui,ml,storage,astro]"
```

Each run is written as a portable bundle containing `telemetry.csv`,
`events.csv`, and `metadata.json`. The metadata records a deterministic run ID, configuration
hash, random seed, mission-source revision, and the complete assumption
manifest used by the run.

## Databricks integration boundary

Simulation code writes through the `RunStorage` protocol. The included
`LocalRunStorage` keeps local development simple; a later Databricks adapter can
write the same telemetry and metadata contracts to Delta tables while MLflow
tracks experiments. Lakebase should hold application-facing state such as run
annotations and saved views, rather than becoming a dependency of the physics
engine.

## Project document

See `docs/A3_DockLab_Project_Plan.md` for the full system design, equations, library choices, testing plan, implementation roadmap, and known risks.

Phase 3 architecture and operating details are documented in
`docs/phase_3_telemetry_replay_plan.md`, with machine-readable Lakehouse and
local-artifact contracts in `docs/contracts/phase_3_tables.yaml`.
The credibility-first physics work and interpretation limits are documented in
`docs/physics_credibility.md`.

## Status

Phases 1-3 now provide controlled rendezvous, synthetic telemetry, and mission replay.
The current Physics Credibility increment adds tested LVLH/ECI frame transforms,
numerical two-body propagation, quaternion attitude dynamics, explicit docking-port
frames, terminal alignment safety, and a reproducible CW validity-envelope report. Both reference scenarios
execute their authorized approach and hold-point sequence through soft capture.
Deterministic closing-rate and corridor violations command braking or retreat
aborts. Versioned multi-rate bundles preserve source and receive timestamps,
fault labels, Parquet streams, and JSONL events for the Dash replay app and
future Databricks adapters. Core Physics Credibility is complete and Phase B now
includes its authority token, validated exchange, dual shadow commands, continuity
monitoring, sustained selected-owner control, and rollback fault cases. Phase B
is complete; Phase C now includes EKF estimation and reproducible local Monte
Carlo risk analysis with structured fault sampling and estimator-consistency
tails. Delta Lake storage/replay adapters now preserve the same contract for
local and Databricks-backed replay. A Databricks bundle now packages the replay
App, serverless simulation and Monte Carlo Jobs, MLflow experiment, and dev/prod
targets. The App now queries Delta replay tables through its bound SQL warehouse
using runtime OAuth and falls back to local bundles for development. Lakebase
stores annotations, saved views, review state, and run comparisons. Optional
orbital perturbations remain planned. All vehicle values are public-source estimates or explicit engineering
placeholders.

## License

MIT. NASA, Artemis, Orion, Blue Moon, Blue Origin, Starship, and SpaceX names remain the property of their respective owners. This project is unaffiliated with and not endorsed by those organizations.
