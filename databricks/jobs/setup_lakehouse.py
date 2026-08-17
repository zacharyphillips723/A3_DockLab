"""Create the Unity Catalog schema required by A3 DockLab Delta writers."""

from __future__ import annotations

import argparse
import re

from pyspark.sql import SparkSession

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quoted(value: str) -> str:
    if IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"Unsafe Unity Catalog identifier {value!r}")
    return f"`{value}`"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    arguments = parser.parse_args()
    spark = SparkSession.getActiveSession()
    if spark is None:
        raise RuntimeError("An active SparkSession is required")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {quoted(arguments.catalog)}.{quoted(arguments.schema)}")


if __name__ == "__main__":
    main()
