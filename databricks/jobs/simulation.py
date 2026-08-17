"""Databricks Job entry point for a simulation and Delta publication."""

from __future__ import annotations

import argparse
from pathlib import Path

from pyspark.sql import SparkSession

from a3docklab.config import load_config
from a3docklab.platform.delta import DeltaRunStorage, SparkDeltaCatalog
from a3docklab.run_metadata import build_run_metadata, load_assumptions, load_source_revision
from a3docklab.simulation.engine import run_controlled, summarize
from a3docklab.telemetry.contracts import FaultConfig, load_telemetry_config, phase3_identity
from a3docklab.telemetry.generator import generate_streams


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "configs/scenarios" / f"{arguments.scenario}.yaml")
    telemetry_config = load_telemetry_config(root / "configs/telemetry/default.yaml")
    faults = FaultConfig()
    result = run_controlled(config)
    metadata = build_run_metadata(
        config,
        load_source_revision(root / "docs/mission_facts.yaml"),
        load_assumptions(root / "docs/assumption_register.csv"),
    )
    run_id, digest = phase3_identity(config.name, metadata.config_sha256, telemetry_config, faults)
    metadata = metadata.model_copy(update={"run_id": run_id, "config_sha256": digest})
    streams = generate_streams(
        result, telemetry_config, faults, random_seed=config.random_seed, run_id=run_id
    )
    spark = SparkSession.getActiveSession()
    if spark is None:
        raise RuntimeError("An active SparkSession is required")
    prefix = f"{arguments.catalog}.{arguments.schema}.a3docklab"
    DeltaRunStorage(SparkDeltaCatalog(spark), prefix).write_bundle(
        streams, metadata, summarize(result).terminal_phase
    )


if __name__ == "__main__":
    main()
