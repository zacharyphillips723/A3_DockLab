"""Databricks App entry point for A3 DockLab mission replay."""

from __future__ import annotations

import os
from pathlib import Path

from a3docklab.analysis.risk import (
    DeltaRiskStore,
    LocalRiskSampleMaterializer,
    LocalRiskStore,
    RiskSampleMaterializer,
    RiskStore,
)
from a3docklab.application.api import register_simulation_routes, register_state_routes
from a3docklab.application.materialization import QueuedSessionMaterializer
from a3docklab.application.sessions import InteractiveSimulationService
from a3docklab.application.state import ApplicationStateStore, PostgresConnectionFactory
from a3docklab.config import load_config
from a3docklab.platform.delta import (
    DatabricksSqlExecutor,
    DeltaReplayStore,
    SqlWarehouseDeltaCatalog,
)
from a3docklab.platform.jobs import (
    DatabricksRiskSampleMaterializer,
    DatabricksSessionMaterializationLauncher,
)
from a3docklab.visualization.dashboard import create_app
from a3docklab.visualization.replay import LocalReplayStore


def replay_store() -> DeltaReplayStore | LocalReplayStore:
    host = os.getenv("DATABRICKS_HOST")
    warehouse_id = os.getenv("A3DOCKLAB_WAREHOUSE_ID")
    if host and warehouse_id:
        catalog = os.environ.get("A3DOCKLAB_CATALOG", "main")
        schema = os.environ.get("A3DOCKLAB_SCHEMA", "a3docklab")
        executor = DatabricksSqlExecutor(host, warehouse_id)
        return DeltaReplayStore(SqlWarehouseDeltaCatalog(executor), f"{catalog}.{schema}.a3docklab")
    return LocalReplayStore(Path(os.getenv("A3DOCKLAB_BUNDLE_ROOT", "bundles")))


def risk_store() -> RiskStore:
    host = os.getenv("DATABRICKS_HOST")
    warehouse_id = os.getenv("A3DOCKLAB_WAREHOUSE_ID")
    if host and warehouse_id:
        catalog = os.environ.get("A3DOCKLAB_CATALOG", "main")
        schema = os.environ.get("A3DOCKLAB_SCHEMA", "a3docklab")
        executor = DatabricksSqlExecutor(host, warehouse_id)
        return DeltaRiskStore(SqlWarehouseDeltaCatalog(executor), f"{catalog}.{schema}.a3docklab")
    return LocalRiskStore(Path(os.getenv("A3DOCKLAB_ENSEMBLE_ROOT", "ensembles")))


def risk_materializer() -> RiskSampleMaterializer | None:
    if os.getenv("DATABRICKS_HOST") and os.getenv("A3DOCKLAB_WAREHOUSE_ID"):
        job_id = os.getenv("A3DOCKLAB_RISK_MATERIALIZATION_JOB_ID")
        return DatabricksRiskSampleMaterializer(job_id) if job_id else None
    return LocalRiskSampleMaterializer(
        Path(os.getenv("A3DOCKLAB_ENSEMBLE_ROOT", "ensembles")),
        Path(os.getenv("A3DOCKLAB_BUNDLE_ROOT", "bundles")),
        Path(__file__).parent,
    )


def application_state_store() -> ApplicationStateStore | None:
    if not all(os.getenv(name) for name in ("PGHOST", "PGDATABASE", "PGUSER")):
        return None
    store = ApplicationStateStore(PostgresConnectionFactory())
    store.initialize()
    return store


scenario_root = Path(__file__).parent / "configs" / "scenarios"
state_store = application_state_store()
artifact_root = os.getenv("A3DOCKLAB_SESSION_ARTIFACT_ROOT")
materialization_job_id = os.getenv("A3DOCKLAB_SESSION_MATERIALIZATION_JOB_ID")
session_materializer = (
    QueuedSessionMaterializer(
        artifact_root,
        DatabricksSessionMaterializationLauncher(materialization_job_id),
    )
    if artifact_root and materialization_job_id
    else None
)
simulation_service = InteractiveSimulationService(
    {path.stem: load_config(path) for path in sorted(scenario_root.glob("*.yaml"))},
    state_store=state_store,
    materializer=session_materializer,
)
app = create_app(
    replay_store(),
    simulation_service.list_scenarios(),
    risk_store(),
    risk_materializer(),
    state_store,
)
server = app.server
register_state_routes(server, lambda: state_store)
register_simulation_routes(server, simulation_service)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DATABRICKS_APP_PORT", "8000")), debug=False)
