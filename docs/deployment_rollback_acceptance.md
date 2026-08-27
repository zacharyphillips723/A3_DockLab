# Deployment, migration, and rollback acceptance

## Pre-deployment

1. Review the bundle diff, dependency audit, schema compatibility tests, and
   Lakebase migration code. Back up Lakebase before a production schema change.
2. Run `ruff check .` and `pytest` from a clean checkout.
3. Run `databricks bundle validate -t dev` with the target warehouse variable.

## Deployment acceptance

```bash
databricks bundle deploy -t dev
databricks bundle run -t dev grant
databricks bundle run -t dev smoke
```

Then open the App and prove: Live create/step/pause; client-local Replay;
bounded Risk; schema-aware Compare; annotation/view/disposition Review; audit
download; operational metrics; session quota rejection; and owner-authorized
restart recovery. Record the bundle commit, target, App URL, smoke run ID, and
test evidence in the release ticket.

## Migration acceptance

`ApplicationStateStore.initialize()` is idempotent. New nullable/defaulted
columns must preserve legacy rows; a migration test is required before deploy.
Apply additive changes first, deploy compatible code second, and remove old
columns only in a later release after a backup and explicit approval.

## Rollback exercise

1. Stop new sessions and retain App, Job, and Lakebase evidence.
2. Redeploy the previously accepted Git commit with the same bundle target.
3. Do not reverse an additive Lakebase migration during the application
   rollback; the older release must tolerate the additional column/table.
4. Run the smoke gate and restore a checkpoint created before rollback.
5. Verify the accepted-command log and review-history count are unchanged.
6. Re-enable normal quotas only after health and operational metrics stabilize.

Rollback succeeds only when the previous App version starts, the smoke gate
passes, an existing session recovers deterministically, and no accepted command
or review transition is lost.
