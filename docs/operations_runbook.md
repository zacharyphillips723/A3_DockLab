# Operations runbook

## Observe

Poll `GET /api/health` for App and Lakebase readiness and
`GET /api/operations/metrics` for request latency, active sessions, simulation
steps, policy latency, safety interventions, dropped frames, reconnects, and
storage-health latency. Correlate a problem with Databricks App logs, Lakebase
metrics, SQL Warehouse query history, and the relevant materialization Job run.

## Quotas and cost controls

`A3DOCKLAB_MAX_ACTIVE_SESSIONS` defaults to 32 per App process and
`A3DOCKLAB_MAX_OWNER_SESSIONS` defaults to 4. Analysis calls retain row, point,
and timeout budgets. Simulation and materialization Jobs also have explicit
`max_concurrent_runs` limits in the bundle.

## Retention cleanup

Preview terminal sessions older than 30 days from an authenticated environment
with the App's Lakebase variables:

```bash
uv run --extra databricks python databricks/cleanup_state.py --retention-days 30
```

Apply only after reviewing the count:

```bash
uv run --extra databricks python databricks/cleanup_state.py --retention-days 30 --apply
```

Cleanup deletes only `complete` or `terminated` session projections and their
accepted-command rows in one transaction. It never deletes active sessions,
Lakehouse run artifacts, review history, annotations, or saved views. Schedule
the apply command in an operator-owned Workflow only after binding a least-
privilege Lakebase identity.

## Incident response

1. Stop new sessions by setting both session limits to 1 and redeploying.
2. Preserve App logs, operational metrics, affected session IDs, audit exports,
   and Job run links.
3. Do not delete leases or accepted commands. Allow lease expiry, then use the
   owner-authorized restore endpoint.
4. If integrity is uncertain, stop the App and follow the rollback runbook.
5. Record cause, affected artifacts, recovery evidence, and corrective action.

