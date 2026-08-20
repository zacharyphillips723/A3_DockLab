# Milestone 5 — Databricks session control plane

Milestone 5 moves live-session coordination across a durable boundary without
moving the deterministic physics loop out of the application process. The
first implementation slice establishes the Lakebase-compatible records that a
Databricks App uses for ownership, recovery, and command admission.

## Implemented foundation

- `simulation_sessions` stores owner, scenario, lifecycle, version, lease, and
  the latest JSON checkpoint.
- Lease acquisition is one atomic, version-checked update. A different holder
  can acquire authority only after the existing lease expires.
- Only a SHA-256 digest of a lease token is persisted.
- Checkpoint writes use optimistic concurrency and reject stale writers.
- `accepted_commands` enforces unique command IDs and idempotency keys per
  session. Retrying the same payload returns the original command; reusing a
  key for another payload fails closed.
- SQLite exercises the same SQL contract used by Lakebase, including reopening
  the database to verify durable recovery metadata.

## Remaining integration slices

1. Bind `InteractiveSimulationService` creation and control requests to this
   repository, including lease renewal and actor authorization.
2. Serialize the complete engine checkpoint and reconstruct a session after an
   App restart, then test process-to-process takeover after lease expiry.
3. Materialize telemetry, decisions, command history, and completed-run
   manifests to Delta with operator and policy lineage.
4. Connect MLflow policy identity/evaluation records and Databricks Jobs for
   asynchronous evaluation and post-run materialization.

The local in-memory execution path remains available so physics and UI work can
be developed without a Databricks workspace.
