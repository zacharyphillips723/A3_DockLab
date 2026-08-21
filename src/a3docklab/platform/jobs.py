"""Databricks Jobs adapters kept outside the application domain."""

from __future__ import annotations


class DatabricksSessionMaterializationLauncher:
    """Submit a completed-session publication Job without blocking the live loop."""

    def __init__(self, job_id: str) -> None:
        self.job_id = int(job_id)

    def launch(self, session_id: str, artifact_root: str) -> None:
        try:
            from databricks.sdk import WorkspaceClient
        except ImportError as exc:
            raise RuntimeError(
                "Databricks Job launch requires the 'databricks' optional dependency"
            ) from exc
        WorkspaceClient().jobs.run_now(
            job_id=self.job_id,
            job_parameters={"session_id": session_id, "artifact_root": artifact_root},
        )
