"""Databricks Jobs adapters kept outside the application domain."""

from __future__ import annotations

from a3docklab.analysis.risk import MaterializationStatus


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


class DatabricksRiskSampleMaterializer:
    """Submit and observe bounded ensemble-sample materialization Jobs."""

    def __init__(self, job_id: str) -> None:
        self.job_id = int(job_id)

    @staticmethod
    def _client() -> object:
        try:
            from databricks.sdk import WorkspaceClient
        except ImportError as exc:
            raise RuntimeError(
                "Databricks Job launch requires the 'databricks' optional dependency"
            ) from exc
        return WorkspaceClient()

    def materialize_sample(self, ensemble_id: str, sample_index: int) -> MaterializationStatus:
        client = self._client()
        response = client.jobs.run_now(  # type: ignore[attr-defined]
            job_id=self.job_id,
            job_parameters={"ensemble_id": ensemble_id, "sample_index": str(sample_index)},
        )
        operation_id = str(response.run_id)
        return MaterializationStatus(operation_id, "queued", detail="Databricks Job queued")

    def materialization_status(self, operation_id: str) -> MaterializationStatus:
        run = self._client().jobs.get_run(int(operation_id))  # type: ignore[attr-defined]
        life_cycle = str(getattr(run.state, "life_cycle_state", "unknown")).lower()
        result = str(getattr(run.state, "result_state", "")).lower()
        if any(value in life_cycle for value in ("pending", "queued", "blocked")):
            state = "queued"
        elif any(value in life_cycle for value in ("running", "terminating")):
            state = "running"
        elif "success" in result:
            state = "completed"
        else:
            state = "failed" if "terminated" in life_cycle else life_cycle
        detail = str(getattr(run.state, "state_message", ""))
        return MaterializationStatus(operation_id, state, detail=detail)
