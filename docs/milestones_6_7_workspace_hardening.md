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

## Combined acceptance gate

- Every displayed result resolves to immutable stored artifacts and config.
- Schema/duration mismatch behavior is explicit and tested.
- Review/audit history is append-only and attributable.
- Published service-level targets pass concurrent load and recovery exercises.
- Security, migration, cleanup, deployment, rollback, and audit-export
  checklists are complete for the target workspace.
