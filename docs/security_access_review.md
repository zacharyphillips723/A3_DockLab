# Security threat model and access review

## Trust boundaries

- The Databricks reverse proxy authenticates users and supplies
  `X-Forwarded-Email`; direct public exposure of the Flask server is unsupported.
- A random control token grants possession of a session lease, but durable
  commands additionally require the forwarded owner identity and an
  idempotency key.
- The App service principal receives only SQL Warehouse use, Lakebase database
  access, selected Job run permissions, and MLflow experiment edit access.
- Lakehouse artifacts are immutable analysis inputs; Lakebase contains mutable
  collaboration projections and append-only review history.

## Principal threats and controls

| Threat | Control | Residual action |
|---|---|---|
| Stolen control token | short lease, token hash at rest, owner check | rotate App/restart session |
| Replay or duplicate command | per-session idempotency key and payload check | investigate conflicts |
| Cross-user view access | owner-scoped saved views/comparisons | add group policy before broader sharing |
| Unsafe model action | deterministic command arbiter and safety envelope | review intervention telemetry |
| Resource exhaustion | session, Job, row, point, and timeout quotas | tune per target after load test |
| Artifact/schema confusion | immutable hashes and explicit schema mapping | reject unknown major versions |
| Audit tampering | append-only review transitions and export package | ship logs to governed sink in production |
| Dependency compromise | weekly `pip-audit` and Dependabot | triage high/critical alerts before release |

## Release access checklist

- Confirm the App URL is workspace-authenticated and not directly proxied.
- Confirm service-principal grants match `resources/app.yml`; remove inherited
  grants not required by the bundle.
- Confirm prod users can read only intended catalogs, schemas, volumes, Jobs,
  and experiments.
- Confirm no secret, bearer token, or database password appears in source,
  bundle variables, logs, audit exports, or accepted-command payloads.
- Run dependency audit and review open security advisories.
- Export one attributed review and verify another user cannot restore the
  owner's saved views or comparisons.

