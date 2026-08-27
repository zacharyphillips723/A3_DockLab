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
- Interactive human and policy-driven Live Lab sessions
- Durable Lakebase session ownership, expiring leases, idempotent commands,
  versioned engine checkpoints, and cross-instance restart recovery
- Delta-backed replay and Databricks Asset Bundle deployment

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

## Current architecture

The deterministic physics and safety core is portable Python and has no
Databricks dependency. `InteractiveSimulationService` provides the live control
boundary used by humans and policy adapters. It runs in memory locally and can
optionally bind session ownership, leases, commands, and versioned recovery
checkpoints to Lakebase. Local bundles or Delta tables provide replay data;
MLflow records model identity and evaluation lineage; Databricks Jobs handle
offline simulation, Monte Carlo, and materialization rather than the live
control loop.

See [`docs/current_architecture.md`](docs/current_architecture.md) for component
boundaries, runtime flows, storage responsibilities, APIs, repository layout,
deployment topology, and the documentation index.

## Deploying to Databricks

The bundle deploys to a development workspace with the following sequence. It
requires the Databricks CLI, an authenticated profile, permission to deploy
Apps/Jobs/experiments/Lakebase resources and to grant on the target catalog,
and an existing SQL warehouse ID.

```bash
databricks auth login --host https://<workspace-host>

# Fresh workspace: create the Lakebase instance first, then bootstrap the
# PostgreSQL database required by the App resource binding.
databricks bundle deploy -t dev --select database_instances.app_state \
  --var="catalog=<catalog>,warehouse_id=<warehouse-id>"
databricks bundle run -t dev \
  --var="catalog=<catalog>,warehouse_id=<warehouse-id>" bootstrap

# Deploy the complete bundle after the database exists.
databricks bundle deploy -t dev \
  --var="catalog=<catalog>,warehouse_id=<warehouse-id>"

# Provision + grant the App's service principal (UC USE CATALOG / USE SCHEMA /
# SELECT and Lakebase Postgres USAGE, CREATE). Also creates the UC schema and
# verifies the Lakebase database exists. Idempotent.
databricks bundle run -t dev \
  --var="catalog=<catalog>,warehouse_id=<warehouse-id>" grant

# Populate Delta replay tables so the App has data on startup.
databricks bundle run -t dev \
  --var="catalog=<catalog>,warehouse_id=<warehouse-id>" simulation

# Deploy the App source and start its compute.
databricks bundle run -t dev \
  --var="catalog=<catalog>,warehouse_id=<warehouse-id>" mission_replay

# Acceptance gate.
databricks bundle run -t dev \
  --var="catalog=<catalog>,warehouse_id=<warehouse-id>" smoke
```

The `catalog` variable must name a catalog you can grant on; `main` is the
default. The App's service principal is created by `bundle deploy`, so `grant`
must run after it. The App connects to Lakebase Postgres directly, so the
database is not registered as a Unity Catalog catalog. To add that registration
(for querying Lakebase through the SQL Warehouse), define a `database_catalogs`
resource; it requires `CREATE CATALOG` on the metastore.

## Documentation

Start with:

- [`docs/current_architecture.md`](docs/current_architecture.md) — current
  implementation and navigation map
- [`docs/interactive_docklab_milestones.md`](docs/interactive_docklab_milestones.md)
  — approved interactive-product roadmap and acceptance gates
- [`docs/A3_DockLab_Project_Plan.md`](docs/A3_DockLab_Project_Plan.md) — original
  system design, equations, library choices, test plan, and risks
- [`docs/milestone_5_control_plane.md`](docs/milestone_5_control_plane.md) —
  current Lakebase control-plane implementation and remaining work
- [`docs/databricks_bundle.md`](docs/databricks_bundle.md) and
  [`docs/databricks_integration.md`](docs/databricks_integration.md) — workspace
  deployment and platform integration

Phase 3 architecture and operating details are documented in
`docs/phase_3_telemetry_replay_plan.md`, with machine-readable Lakehouse and
local-artifact contracts in `docs/contracts/phase_3_tables.yaml`.
The credibility-first physics work and interpretation limits are documented in
`docs/physics_credibility.md`.

## Status

Physics Credibility and Phases A-C are complete, including rendezvous/control,
attitude and docking-port dynamics, controller handoff, EKF estimation, and
reproducible Monte Carlo risk analysis. Interactive Milestones 1-7 are complete.
The product includes Live, Replay, Risk, Compare, and Review workspaces plus
operational telemetry, resource quotas, retention cleanup, security/dependency
checks, and documented deployment and rollback gates. Milestone 5's
Lakebase-backed ownership, expiring control leases,
idempotent commands, optimistic concurrency, versioned engine checkpoints,
cross-instance restart recovery, asynchronous Delta materialization, and
MLflow completion lineage are implemented behind the workspace smoke gate. The
code-level Milestone 7 gates pass locally; each target workspace still runs the
documented smoke and rollback exercise as release evidence.

The DAB packages the Databricks App, Lakebase database, SQL warehouse binding,
serverless simulation and Monte Carlo Jobs, MLflow experiment, and dev/prod
targets. Workspace releases use `databricks bundle run -t dev smoke` as the
post-deployment acceptance gate. All vehicle values are public-source estimates
or explicit engineering placeholders. Optional orbital perturbations remain
planned.

## License

MIT. NASA, Artemis, Orion, Blue Moon, Blue Origin, Starship, and SpaceX names remain the property of their respective owners. This project is unaffiliated with and not endorsed by those organizations.
