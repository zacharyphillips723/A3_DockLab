"""Databricks Job entry point for completed interactive-session publication."""

from __future__ import annotations

import argparse
from pathlib import Path

import mlflow
import pandas as pd
from pyspark.sql import SparkSession

from a3docklab.application.materialization import (
    CompletedSessionArtifact,
    InteractiveSessionManifest,
)
from a3docklab.platform.delta import DeltaSessionMaterializer, SparkDeltaCatalog


def _read_frame(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--experiment", required=True)
    arguments = parser.parse_args()
    source = Path(arguments.artifact_root) / arguments.session_id
    manifest = InteractiveSessionManifest.model_validate_json(
        (source / "manifest.json").read_text(encoding="utf-8")
    )
    artifact = CompletedSessionArtifact(
        manifest=manifest,
        telemetry=_read_frame(source / "telemetry.csv"),
        events=_read_frame(source / "events.csv"),
        decisions=_read_frame(source / "decisions.csv"),
        commands=_read_frame(source / "commands.csv"),
        policy_evaluations=_read_frame(source / "policy_evaluations.csv"),
    )
    spark = SparkSession.getActiveSession()
    if spark is None:
        raise RuntimeError("An active SparkSession is required")
    prefix = f"{arguments.catalog}.{arguments.schema}.a3docklab"
    DeltaSessionMaterializer(SparkDeltaCatalog(spark), prefix).materialize(artifact)

    mlflow.set_experiment(arguments.experiment)
    with mlflow.start_run(run_name=f"interactive-{manifest.session_id}"):
        mlflow.log_params(
            {
                "session_id": manifest.session_id,
                "run_id": manifest.run_id,
                "scenario_id": manifest.scenario_id,
                "owner": manifest.owner,
                "lifecycle": manifest.lifecycle,
                "active_policy_id": manifest.active_policy_id or "human",
                "shadow_policy_id": manifest.shadow_policy_id or "none",
                "model_uri": manifest.model_uri or "none",
                "model_version": manifest.model_version,
                "code_revision": manifest.code_revision,
                "policy_runtime_json": manifest.policy_runtime_json,
            }
        )
        mlflow.log_metrics(
            {
                "telemetry_rows": manifest.telemetry_rows,
                "event_rows": manifest.event_rows,
                "decision_rows": manifest.decision_rows,
                "command_rows": manifest.command_rows,
                "policy_evaluation_rows": manifest.policy_evaluation_rows,
            }
        )
        mlflow.log_artifact(source / "manifest.json")


if __name__ == "__main__":
    main()
