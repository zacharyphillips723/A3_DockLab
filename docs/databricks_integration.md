# Databricks Integration Boundary

A3 DockLab keeps simulation, control, safety, and anomaly logic independent of
its deployment platform. Platform integration belongs behind explicit adapters.

## Proposed data responsibilities

- **Delta Lake / Lakehouse:** immutable telemetry samples, events, run metadata,
  assumption snapshots, fault catalogs, and Monte Carlo summaries.
- **MLflow:** experiment parameters, metrics, model artifacts, signatures, and
  promotion state for anomaly detectors.
- **Lakebase:** mutable application data such as user annotations, saved views,
  review status, and dashboard preferences.
- **Databricks Jobs:** distributed Monte Carlo execution, feature generation,
  detector training, and evaluation.
- **Databricks App:** mission replay and alert investigation using the same
  application services as local development.

## Initial table contracts

The first adapter should map the local run bundle into normalized tables:

- `simulation_runs`: one row per run, keyed by `run_id`.
- `telemetry_samples`: time-series samples keyed by `run_id` and `time_s`.
- `assumption_snapshots`: assumptions keyed by `run_id` and `assumption_id`.
- `mission_events`: phase changes, warnings, aborts, faults, and docking events.

The physics engine must not import Databricks SDKs. A Delta-backed implementation
of `RunStorage` can be selected by application configuration or dependency
injection when workspace plumbing is added.
