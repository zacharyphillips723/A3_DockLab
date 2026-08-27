# Milestones 6 and 7 — Workspace and hardening program

Milestones 6 and 7 are implemented as one coordinated program. Every Risk,
Compare, or Review capability must ship with reproducibility, authorization,
bounded resource use, observability, migration behavior, and rollback evidence
rather than receiving those controls afterward.

## Joint workstreams

1. **Versioned analysis contracts and immutable audit history** — artifact
   references include run ID, schema version, configuration hash, and source
   URI. Mismatched schemas require explicit mappings. Review transitions append
   immutable attributed history while a current-state projection supports UI.
2. **Risk workspace and operational budgets** — Monte Carlo distributions,
   success/abort rates, convergence, and sensitivity share enforced row, trace,
   and timeout limits plus query-latency/storage-lag telemetry.
3. **Comparison and reproducibility** — trajectories, KPIs, commands, safety
   interventions, and policy versions align by event time or mission phase.
   Saved comparisons retain artifact identity and compatibility mappings.
4. **Review workflow and audit export** — pinned annotations, saved views,
   approval/rejection history, snapshots, and share links retain actor identity
   and immutable source references.
5. **Resilience and security** — load/soak/recovery tests, schema migrations,
   quotas, expiration/cleanup, threat model, dependency scanning, access review,
   service targets, deployment runbooks, and rollback exercises.

## First implementation slice

- `AnalysisArtifactRef`, `ComparisonSpec`, `ReproducibleReviewRef`, and bounded
  `AnalysisBudget` establish the shared API/data boundary.
- Lakebase keeps the existing current `run_reviews` projection and now appends
  every transition to `run_review_history` in the same transaction.
- Tests reject unbounded analysis requests and mismatched schemas without an
  explicit compatibility map, and prove review history remains attributable.

## Risk workspace slice

- A storage-neutral Risk service reads the same immutable ensemble contract
  from local Parquet/JSON artifacts or the Databricks Delta ensemble tables.
- The Risk tab presents capture/abort KPIs, closest-approach and propellant
  distributions, convergence, and a transparent correlation-based sensitivity
  screen. It retains schema, configuration hash, source URI, rows scanned, and
  query latency beside every selected ensemble.
- `AnalysisBudget` limits rows, elapsed query time, and rendered convergence
  points. Local execution and Databricks SQL use the same page contract.
- The remaining Risk work is richer contact-condition metrics, categorical
  fault sensitivity, ensemble-to-run drill-through, and persisted operational
  telemetry/service targets.

### Risk detail slice

- New ensembles retain deterministic sample `run_id` plus closing rate,
  lateral offset, angular error, and dissipated energy at capture. Older
  ensembles continue to render with an explicit unavailable state.
- Fault-conditioned capture/abort outcomes and contact-condition distributions
  make categorical faults and docking quality directly explorable.
- A ranked outlier table links to Replay when the deterministic sample run has
  been materialized in the replay store; otherwise it explicitly reports
  `Not materialized` rather than creating a broken or non-reproducible link.
- URL workspace/run restoration supports links of the form
  `?workspace=replay&run_id=...` and `?workspace=risk`.

### On-demand sample materialization

- Ensemble manifests now embed the validated base scenario, ensemble sampling
  configuration, and telemetry configuration. A later rebuild never silently
  substitutes a changed project YAML file.
- Local Risk links rebuild exactly one selected sample, write a versioned Phase
  3 bundle, refresh the replay catalog, and redirect to its deep-linked Replay.
- Materialization validates ensemble and sample identity before filesystem
  access. Older ensembles without immutable rebuild inputs fail explicitly.
- The `RiskSampleMaterializer` boundary keeps generation out of the dashboard.
  In Databricks the App launches a dedicated, concurrency-bounded DAB Job,
  reports queued/running/completed/failed state, and writes the selected sample
  through `DeltaRunStorage`. The App service principal receives only the
  corresponding `CAN_MANAGE_RUN` job resource permission.

## Compare workspace slice

- A storage-neutral comparison service uses the same local/Delta `ReplayStore`
  contract and retains both run IDs, schema versions, configuration hashes, and
  source locations in a validated `ComparisonSpec`.
- Event-time alignment uses only the declared overlap interval. Mission-phase
  alignment compares shared phase progress explicitly; runs with no common
  interval or phase fail rather than producing misleading plots.
- The Compare tab overlays both 3D trajectories and aligned range, closing
  rate, keep-out margin, and corridor margin. It reports candidate-minus-
  baseline deltas for duration, closest approach, closing rate, propellant,
  safety violations, and command-source transitions.
- Schema mismatches require a candidate-to-baseline JSON channel mapping.
  Row, trace-point, and timeout budgets are enforced before results render.

The next Compare slice is persisted `SavedComparison` state plus richer command,
policy-version, safety-intervention, and event-level diff tables. That slice
joins naturally with the Review workspace because saved comparisons need actor,
annotation, approval, and immutable view-state history.

### Saved comparison and detailed-diff slice

- Compare now counts command sources, controller authorities, policy IDs and
  versions when present, mission-event types, and safety-margin violations for
  baseline and candidate artifacts. The detailed table reports explicit deltas
  beside the existing aligned plots and KPI scorecard.
- Owner-scoped saved comparisons retain the complete validated
  `ComparisonSpec`, including immutable artifact references, alignment, schema
  mapping, and analysis budget. Saving or restoring never resolves only by a
  mutable display name.
- Lakebase adds `comparison_spec_json` through a tested backward-compatible
  migration. The HTTP API validates saved specs and derives owner identity from
  the Databricks forwarded user header.

The remaining Milestone 6 work is the Review workspace: pinned annotations,
saved replay views, approval/rejection controls, immutable history, audit
export, and shareable reproducible view links. Milestone 7 then closes with
observability/service targets, quotas and cleanup, load/soak/recovery tests,
security review, and deployment/rollback exercises.

## Combined acceptance gate

- Every displayed result resolves to immutable stored artifacts and config.
- Schema/duration mismatch behavior is explicit and tested.
- Review/audit history is append-only and attributable.
- Published service-level targets pass concurrent load and recovery exercises.
- Security, migration, cleanup, deployment, rollback, and audit-export
  checklists are complete for the target workspace.
