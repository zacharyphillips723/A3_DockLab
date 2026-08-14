"""Databricks Job entry point for Monte Carlo execution and MLflow reporting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import mlflow
import pandas as pd
from pyspark.sql import SparkSession

from a3docklab.config import load_config
from a3docklab.platform.delta import SparkDeltaCatalog
from a3docklab.simulation.monte_carlo import load_monte_carlo_config, run_ensemble
from a3docklab.telemetry.contracts import load_telemetry_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--ensemble-config", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--experiment", required=True)
    arguments = parser.parse_args()
    root = Path.cwd()
    scenario = load_config(root / "configs/scenarios" / f"{arguments.scenario}.yaml")
    config = load_monte_carlo_config(
        root / "configs/monte_carlo" / f"{arguments.ensemble_config}.yaml"
    )
    telemetry = load_telemetry_config(root / "configs/telemetry/default.yaml")
    result = run_ensemble(scenario, config, telemetry)
    ensemble_id = str(result.manifest["ensemble_id"])
    spark = SparkSession.getActiveSession()
    if spark is None:
        raise RuntimeError("An active SparkSession is required")
    catalog = SparkDeltaCatalog(spark)
    prefix = f"{arguments.catalog}.{arguments.schema}.a3docklab"
    for suffix, frame in (("ensemble_runs", result.runs), ("ensemble_convergence", result.convergence)):
        payload = frame.copy()
        payload.insert(0, "ensemble_id", ensemble_id)
        catalog.append_table(f"{prefix}_{suffix}", payload)
    catalog.append_table(
        f"{prefix}_ensembles",
        pd.DataFrame(
            [
                {
                    "ensemble_id": ensemble_id,
                    "manifest_json": json.dumps(result.manifest),
                    "risk_summary_json": json.dumps(result.risk_summary),
                }
            ]
        ),
    )
    mlflow.set_experiment(arguments.experiment)
    with mlflow.start_run(run_name=ensemble_id):
        mlflow.log_params(
            {
                "ensemble_id": ensemble_id,
                "scenario": scenario.name,
                "sample_count": config.sample_count,
                "random_seed": config.random_seed,
            }
        )
        mlflow.log_metrics(
            {key: float(value) for key, value in result.risk_summary.items()}
        )


if __name__ == "__main__":
    main()
