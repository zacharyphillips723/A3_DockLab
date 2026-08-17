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

Phase 3 refines these initial tables into multi-rate truth, navigation,
actuation, communications, event, and label contracts. See
[`phase_3_telemetry_replay_plan.md`](phase_3_telemetry_replay_plan.md) and the
machine-readable [`contracts/phase_3_tables.yaml`](contracts/phase_3_tables.yaml).

## Implemented adapter boundary

`platform.delta` now provides `DeltaRunStorage` and `DeltaReplayStore` over a
small `DeltaCatalog` protocol. `SparkDeltaCatalog` is the production adapter: a
Databricks App or Job injects its existing Spark session, and the adapter uses
Delta append semantics without importing PySpark or Databricks packages into
the simulation domain. `InMemoryDeltaCatalog` exercises the identical contract
in local tests.

Each stream is stored in a normalized table prefixed with `a3docklab_`; every
row receives `run_id`. `a3docklab_runs` stores the versioned bundle manifest,
run metadata, and feature allowlist as JSON so the replay service can discover
streams without filesystem access. Time and run filters are pushed into the
catalog before conversion to pandas, and display decimation happens only after
the filtered query.

The Databricks App uses a read-only `SqlWarehouseDeltaCatalog`. It authenticates
through the App runtime's Databricks credentials, binds to the configured SQL
warehouse, validates every catalog/schema/table/column identifier, and sends
filter values as native query parameters. This preserves run and time predicate
pushdown without exposing write access from the UI process.

Lakebase holds four mutable, transactionally updated entities: time-addressable
run annotations, owner-scoped saved views, reviewer-scoped run dispositions,
and saved baseline/candidate comparisons. The App receives the standard `PG*`
connection variables from its database resource and obtains an OAuth password
from the runtime Databricks identity. Tables are initialized idempotently, and
no mutable state is written into Delta telemetry tables.

Workspace setup may choose a Unity Catalog-qualified table prefix, for example
`main.a3docklab.a3docklab`, by constructing the adapters with that prefix. Table creation,
permissions, cluster policy, and authentication remain deployment plumbing and
can be supplied by the Databricks workspace.
