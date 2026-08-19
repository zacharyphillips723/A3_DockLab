"""Databricks App entry point for A3 DockLab mission replay."""

from __future__ import annotations

import os
from pathlib import Path

from a3docklab.application.api import register_simulation_routes, register_state_routes
from a3docklab.application.sessions import InteractiveSimulationService
from a3docklab.application.state import ApplicationStateStore, PostgresConnectionFactory
from a3docklab.config import load_config
from a3docklab.platform.delta import (
    DatabricksSqlExecutor,
    DeltaReplayStore,
    SqlWarehouseDeltaCatalog,
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


def application_state_store() -> ApplicationStateStore | None:
    if not all(os.getenv(name) for name in ("PGHOST", "PGDATABASE", "PGUSER")):
        return None
    store = ApplicationStateStore(PostgresConnectionFactory())
    store.initialize()
    return store


scenario_root = Path(__file__).parent / "configs" / "scenarios"
simulation_service = InteractiveSimulationService(
    {path.stem: load_config(path) for path in sorted(scenario_root.glob("*.yaml"))}
)
app = create_app(replay_store(), simulation_service.list_scenarios())
state_store = application_state_store()
server = app.server
register_state_routes(server, lambda: state_store)
register_simulation_routes(server, simulation_service)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DATABRICKS_APP_PORT", "8000")), debug=False)
