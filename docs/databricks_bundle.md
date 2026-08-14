# Databricks Bundle Deployment

A3 DockLab is packaged as a Databricks Declarative Automation Bundle. The
bundle deploys a Databricks App, simulation and Monte Carlo Jobs, and an MLflow
experiment while keeping workspace identity and infrastructure values outside
source control.

## Resources

- `mission_replay`: Dash-based Databricks App with permission to trigger both
  Jobs and write to the bundle-managed MLflow experiment.
- `simulation`: serverless Python task that runs a reference scenario,
  generates Phase 3 streams, and appends them to normalized Delta tables.
- `monte_carlo`: serverless Python task that writes ensemble/convergence/risk
  tables and logs aggregate metrics to MLflow.
- `a3docklab`: MLflow experiment scoped beneath the target workspace root.

The `dev` target isolates its Unity Catalog schema by workspace user. The
`prod` target uses the stable `a3docklab` schema and a shared bundle root.

## Deployment inputs

Authenticate the Databricks CLI to the intended workspace, then validate with:

```bash
databricks auth login --host https://<workspace-host>
databricks bundle validate -t dev \
  --var="catalog=<catalog>,warehouse_id=<warehouse-id>"
```

Deploy and run a job with:

```bash
databricks bundle deploy -t dev \
  --var="catalog=<catalog>,warehouse_id=<warehouse-id>"
databricks bundle run -t dev simulation \
  --params scenario=blue_moon_side
```

Production deployment should supply a service-principal `run_as` override and
explicit permissions through the deployment pipeline. Credentials, workspace
URLs, warehouse IDs, and service-principal identifiers must not be committed.

## Current boundary

Jobs publish directly through the injected Spark/Delta catalog. The App receives
catalog, schema, and warehouse identifiers plus resource bindings for the SQL
warehouse, both Jobs, and MLflow. In Databricks it uses OAuth-backed,
parameterized SQL Warehouse queries against the same Delta replay contract; in
local development it falls back to filesystem bundles. Lakebase-backed mutable
review state is the next platform increment.
