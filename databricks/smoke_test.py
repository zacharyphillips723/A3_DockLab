"""End-to-end smoke test for a deployed A3 DockLab development bundle."""

from __future__ import annotations

import argparse
from typing import Any

import requests
from databricks.sdk import WorkspaceClient

from a3docklab.platform.delta import (
    DatabricksSqlExecutor,
    DeltaReplayStore,
    SqlWarehouseDeltaCatalog,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--warehouse-id", required=True)
    arguments = parser.parse_args()

    workspace = WorkspaceClient()
    job_name = f"a3-docklab-simulation-{arguments.target}"
    # In `mode: development` bundles the deployed name carries a "[dev <user>] "
    # prefix, so match on the suffix rather than the exact name.
    job = next(
        (
            candidate
            for candidate in workspace.jobs.list()
            if candidate.settings is not None
            and candidate.settings.name is not None
            and candidate.settings.name.endswith(job_name)
        ),
        None,
    )
    if job is None or job.job_id is None:
        raise RuntimeError(f"Deployed simulation Job {job_name!r} was not found")
    workspace.jobs.run_now(
        job_id=job.job_id, job_parameters={"scenario": "blue_moon_side"}
    ).result()

    replay = DeltaReplayStore(
        SqlWarehouseDeltaCatalog(
            DatabricksSqlExecutor(workspace.config.host, arguments.warehouse_id)
        ),
        f"{arguments.catalog}.{arguments.schema}.a3docklab",
    )
    runs = replay.list_runs()
    if not runs:
        raise RuntimeError("Simulation completed but no Delta replay run was found")
    run_id = runs[-1].run_id
    truth = replay.query_stream(run_id, "truth", max_points=10)
    if truth.empty:
        raise RuntimeError(f"Delta replay returned no truth samples for {run_id}")

    app_name = f"a3-docklab-{arguments.target}"
    deployed_app: Any = workspace.apps.get(app_name)
    app_url = str(deployed_app.url).rstrip("/")
    headers = workspace.config.authenticate()
    health = requests.get(f"{app_url}/api/health", headers=headers, timeout=30)
    health.raise_for_status()
    if not health.json().get("lakebase_ready"):
        raise RuntimeError("App is healthy but Lakebase is not ready")
    created = requests.post(
        f"{app_url}/api/annotations",
        headers=headers,
        json={"run_id": run_id, "text": "Deployment smoke test"},
        timeout=30,
    )
    created.raise_for_status()
    listed = requests.get(
        f"{app_url}/api/annotations",
        headers=headers,
        params={"run_id": run_id},
        timeout=30,
    )
    listed.raise_for_status()
    annotation_id = created.json()["annotation_id"]
    if annotation_id not in {item["annotation_id"] for item in listed.json()}:
        raise RuntimeError("Lakebase annotation round trip failed")
    print(
        f"Smoke test passed: run={run_id}, truth_rows={len(truth)}, "
        f"annotation={annotation_id}, app={app_url}"
    )


if __name__ == "__main__":
    main()
