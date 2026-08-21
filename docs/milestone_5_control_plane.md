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
- `InteractiveSimulationService` optionally binds to the durable repository;
  its original in-memory path remains the offline/local default.
- Session creation establishes a persisted lease, and every control request
  validates the owner, bearer-token digest, lease holder, expiry, and expected
  version before it reaches the physics engine.
- Successful control operations write a fresh lifecycle checkpoint. Browser
  requests include unique idempotency keys, and lease expiry or a competing
  durable writer fails closed.
- Engine checkpoints now use an explicit `1.0` JSON schema containing the
  deterministic state and complete intent sequence. JSON round-trip tests
  verify that replaying this payload produces the same subsequent frame.
- A replacement application instance can restore a session after its lease
  expires. Recovery verifies ownership, atomically claims a new lease and
  token, rebuilds the configured policy runtime, replays the engine checkpoint,
  and preserves paused/running/terminated lifecycle state.
- Cross-instance tests prove that takeover is rejected before expiry, resumes
  from the exact stored frame afterward, and invalidates the previous writer.

## Completed-session publication

- Terminal sessions normalize telemetry, events, decisions, commands, policy
  evaluations, and an attributed `1.0` manifest through one portable contract.
- Local development writes the contract as CSV/JSON. The Databricks App writes
  the same files to a managed Unity Catalog Volume and asynchronously launches
  the `session_materialization` Job.
- The Job appends normalized `a3docklab_interactive_*` Delta tables and logs
  owner, scenario, active/shadow policy, model URI/version, code revision,
  runtime configuration, and row-count metrics to MLflow.
- The grant workflow creates the managed Volume idempotently and grants the App
  service principal read/write access. The App uses a dedicated control-token
  header so workspace OAuth and live authority can coexist.
- The deployment smoke gate terminates a live session and waits for its
  attributed Delta manifest before accepting the release.

## Acceptance evidence

- restart tests reproduce the stored frame across independent service
  instances and invalidate the expired writer;
- lease, identity, optimistic-concurrency, and idempotency tests fail closed;
- local and Delta materializers pass the same artifact-contract tests;
- terminal materialization is idempotent within the live service;
- the DAB packages the App, Lakebase, managed-Volume grant workflow, Spark
  publication Job, SQL access, and MLflow experiment;
- `databricks bundle run -t dev smoke` is the workspace acceptance gate.

The local in-memory execution path remains available so physics and UI work can
be developed without a Databricks workspace.
