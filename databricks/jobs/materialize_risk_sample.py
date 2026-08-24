"""Databricks Job entry point for one on-demand Monte Carlo replay sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pyspark.sql import SparkSession

from a3docklab.analysis.risk import rebuild_risk_sample
from a3docklab.platform.delta import DeltaRunStorage, SparkDeltaCatalog, TableFilter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ensemble-id", required=True)
    parser.add_argument("--sample-index", required=True, type=int)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--assets-root", required=True)
    arguments = parser.parse_args()
    spark = SparkSession.getActiveSession()
    if spark is None:
        raise RuntimeError("An active SparkSession is required")
    catalog = SparkDeltaCatalog(spark)
    prefix = f"{arguments.catalog}.{arguments.schema}.a3docklab"
    metadata_rows = catalog.read_table(
        f"{prefix}_ensembles",
        filters=(TableFilter("ensemble_id", "eq", arguments.ensemble_id),),
    )
    sample_rows = catalog.read_table(
        f"{prefix}_ensemble_runs",
        filters=(
            TableFilter("ensemble_id", "eq", arguments.ensemble_id),
            TableFilter("sample_index", "eq", arguments.sample_index),
        ),
    )
    if len(metadata_rows) != 1 or len(sample_rows) != 1:
        raise KeyError("ensemble or sample was not found")
    rebuilt = rebuild_risk_sample(
        json.loads(str(metadata_rows.iloc[0]["manifest_json"])),
        sample_rows.iloc[0],
        Path(arguments.assets_root),
    )
    DeltaRunStorage(catalog, prefix).write_bundle(
        rebuilt.streams, rebuilt.metadata, rebuilt.terminal_phase
    )
    print(json.dumps({"run_id": rebuilt.run_id, "state": "completed"}))


if __name__ == "__main__":
    main()
