"""Delta Lake catalog, run-storage, and replay adapters.

The production catalog accepts a Databricks ``SparkSession`` by dependency
injection. Importing this module does not require PySpark or a Databricks SDK.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

import pandas as pd

from a3docklab.run_metadata import RunMetadata
from a3docklab.telemetry.contracts import BundleManifest, StreamManifest
from a3docklab.telemetry.generator import TelemetryStreams
from a3docklab.visualization.replay import decimate


@dataclass(frozen=True)
class TableFilter:
    column: str
    operator: str
    value: object


class DeltaCatalog(Protocol):
    def append_table(self, table: str, frame: pd.DataFrame) -> None: ...

    def read_table(
        self,
        table: str,
        *,
        filters: tuple[TableFilter, ...] = (),
        columns: list[str] | None = None,
        order_by: tuple[str, ...] = (),
    ) -> pd.DataFrame: ...


class SqlQueryExecutor(Protocol):
    def execute(self, query: str, parameters: Mapping[str, object]) -> pd.DataFrame: ...


class DatabricksSqlExecutor:
    """PEP 249 query executor using Databricks App OAuth credentials."""

    def __init__(self, server_hostname: str, warehouse_id: str) -> None:
        self.server_hostname = server_hostname.removeprefix("https://").rstrip("/")
        self.warehouse_id = warehouse_id

    def execute(self, query: str, parameters: Mapping[str, object]) -> pd.DataFrame:
        try:
            from databricks.sdk.core import Config

            from databricks import sql
        except ImportError as exc:
            raise RuntimeError("SQL replay requires the 'databricks' optional dependency") from exc
        config = Config()
        with (
            sql.connect(
                server_hostname=self.server_hostname,
                http_path=f"/sql/1.0/warehouses/{self.warehouse_id}",
                credentials_provider=lambda: config.authenticate,
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(query, parameters=parameters)
            return cast(pd.DataFrame, cursor.fetchall_arrow().to_pandas())


class SqlWarehouseDeltaCatalog:
    """Read-only Delta catalog backed by parameterized SQL Warehouse queries."""

    _identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def __init__(self, executor: SqlQueryExecutor) -> None:
        self.executor = executor

    @classmethod
    def _quote_identifier(cls, identifier: str) -> str:
        segments = identifier.split(".")
        if not segments or any(cls._identifier.fullmatch(segment) is None for segment in segments):
            raise ValueError(f"Unsafe SQL identifier {identifier!r}")
        return ".".join(f"`{segment}`" for segment in segments)

    def append_table(self, table: str, frame: pd.DataFrame) -> None:
        raise NotImplementedError("SQL Warehouse replay catalog is read-only")

    def read_table(
        self,
        table: str,
        *,
        filters: tuple[TableFilter, ...] = (),
        columns: list[str] | None = None,
        order_by: tuple[str, ...] = (),
    ) -> pd.DataFrame:
        projection = (
            "*"
            if columns is None
            else ", ".join(self._quote_identifier(column) for column in columns)
        )
        query = f"SELECT {projection} FROM {self._quote_identifier(table)}"
        predicates: list[str] = []
        parameters: dict[str, object] = {}
        operators = {"eq": "=", "ge": ">=", "le": "<="}
        for index, item in enumerate(filters):
            if item.operator not in operators:
                raise ValueError(f"Unsupported filter operator {item.operator!r}")
            parameter = f"filter_{index}"
            predicates.append(
                f"{self._quote_identifier(item.column)} {operators[item.operator]} :{parameter}"
            )
            parameters[parameter] = item.value
        if predicates:
            query += " WHERE " + " AND ".join(predicates)
        if order_by:
            query += " ORDER BY " + ", ".join(self._quote_identifier(column) for column in order_by)
        return self.executor.execute(query, parameters)


class SparkDeltaCatalog:
    """Thin adapter over a SparkSession available in a Databricks workspace."""

    def __init__(self, spark: Any) -> None:
        self.spark = spark

    def append_table(self, table: str, frame: pd.DataFrame) -> None:
        # Spark Connect (serverless) cannot infer a schema from an empty pandas
        # frame, so derive an explicit schema from the pandas dtypes in that case.
        if frame.empty:
            spark_frame = self.spark.createDataFrame(frame, schema=self._infer_schema(frame))
        else:
            spark_frame = self.spark.createDataFrame(frame)
        spark_frame.write.format("delta").mode("append").saveAsTable(table)

    @staticmethod
    def _infer_schema(frame: pd.DataFrame) -> Any:
        import pandas.api.types as ptypes
        from pyspark.sql.types import (
            BooleanType,
            DoubleType,
            LongType,
            StringType,
            StructField,
            StructType,
        )

        fields = []
        for name, dtype in frame.dtypes.items():
            if ptypes.is_bool_dtype(dtype):
                spark_type: Any = BooleanType()
            elif ptypes.is_integer_dtype(dtype):
                spark_type = LongType()
            elif ptypes.is_float_dtype(dtype):
                spark_type = DoubleType()
            else:
                spark_type = StringType()
            fields.append(StructField(str(name), spark_type, True))
        return StructType(fields)

    def read_table(
        self,
        table: str,
        *,
        filters: tuple[TableFilter, ...] = (),
        columns: list[str] | None = None,
        order_by: tuple[str, ...] = (),
    ) -> pd.DataFrame:
        frame = self.spark.table(table)
        for item in filters:
            column = frame[item.column]
            operations = {
                "eq": column == item.value,
                "ge": column >= item.value,
                "le": column <= item.value,
            }
            if item.operator not in operations:
                raise ValueError(f"Unsupported filter operator {item.operator!r}")
            frame = frame.filter(operations[item.operator])
        if columns is not None:
            frame = frame.select(*columns)
        if order_by:
            frame = frame.orderBy(*order_by)
        return cast(pd.DataFrame, frame.toPandas())


class InMemoryDeltaCatalog:
    """Contract-test implementation with Delta append/read semantics."""

    def __init__(self) -> None:
        self.tables: dict[str, pd.DataFrame] = {}

    def append_table(self, table: str, frame: pd.DataFrame) -> None:
        existing = self.tables.get(table)
        self.tables[table] = (
            pd.concat([existing, frame], ignore_index=True)
            if existing is not None
            else frame.copy()
        )

    def read_table(
        self,
        table: str,
        *,
        filters: tuple[TableFilter, ...] = (),
        columns: list[str] | None = None,
        order_by: tuple[str, ...] = (),
    ) -> pd.DataFrame:
        frame = self.tables.get(table, pd.DataFrame()).copy()
        for item in filters:
            if item.operator == "eq":
                frame = frame[frame[item.column] == item.value]
            elif item.operator == "ge":
                frame = frame[frame[item.column] >= item.value]
            elif item.operator == "le":
                frame = frame[frame[item.column] <= item.value]
            else:
                raise ValueError(f"Unsupported filter operator {item.operator!r}")
        if columns is not None:
            frame = frame[columns]
        if order_by:
            frame = frame.sort_values(list(order_by))
        return frame.reset_index(drop=True)


class DeltaRunStorage:
    """Append immutable Phase 3 bundles to normalized Delta tables."""

    def __init__(self, catalog: DeltaCatalog, table_prefix: str = "a3docklab") -> None:
        self.catalog = catalog
        self.table_prefix = table_prefix

    def _table(self, suffix: str) -> str:
        return f"{self.table_prefix}_{suffix}"

    def write_bundle(
        self, streams: TelemetryStreams, metadata: RunMetadata, terminal_phase: str
    ) -> BundleManifest:
        frames = {
            "truth": streams.truth,
            "navigation": streams.navigation,
            "navigation_estimates": streams.navigation_estimates,
            "actuation": streams.actuation,
            "communications": streams.communications,
            "fault_labels": streams.fault_labels,
            "events": streams.events,
        }
        descriptors: list[StreamManifest] = []
        for name, source in frames.items():
            frame = source.copy()
            frame["run_id"] = metadata.run_id
            frame = frame[["run_id", *[column for column in frame if column != "run_id"]]]
            self.catalog.append_table(self._table(name), frame)
            descriptors.append(
                StreamManifest(
                    name=name,
                    path=self._table(name),
                    row_count=len(frame),
                    format="delta",
                    time_column="onset_time_ns" if name == "fault_labels" else "event_time_ns",
                )
            )
        manifest = BundleManifest(
            schema_version="3.0",
            run_id=metadata.run_id,
            scenario_id=metadata.scenario,
            created_at_utc=metadata.created_at_utc,
            configuration_hash=metadata.config_sha256,
            terminal_phase=terminal_phase,
            streams=descriptors,
            feature_allowlist_path=self._table("feature_allowlists"),
        )
        self.catalog.append_table(
            self._table("runs"),
            pd.DataFrame(
                [
                    {
                        **manifest.model_dump(mode="json"),
                        "manifest_json": manifest.model_dump_json(),
                        "metadata_json": metadata.model_dump_json(),
                        "feature_allowlist_json": json.dumps(list(streams.feature_allowlist)),
                    }
                ]
            ),
        )
        return manifest


class DeltaReplayStore:
    """ReplayStore implementation querying normalized Delta stream tables."""

    def __init__(self, catalog: DeltaCatalog, table_prefix: str = "a3docklab") -> None:
        self.catalog = catalog
        self.table_prefix = table_prefix

    def _table(self, suffix: str) -> str:
        return f"{self.table_prefix}_{suffix}"

    def list_runs(self) -> list[BundleManifest]:
        rows = self.catalog.read_table(self._table("runs"), order_by=("created_at_utc",))
        return [
            BundleManifest.model_validate_json(value) for value in rows.get("manifest_json", [])
        ]

    def query_stream(
        self,
        run_id: str,
        stream: str,
        *,
        start_ns: int | None = None,
        end_ns: int | None = None,
        columns: list[str] | None = None,
        max_points: int | None = None,
    ) -> pd.DataFrame:
        manifests = {item.run_id: item for item in self.list_runs()}
        if run_id not in manifests:
            raise KeyError(f"Unknown run {run_id!r}")
        descriptor = next((item for item in manifests[run_id].streams if item.name == stream), None)
        if descriptor is None:
            raise KeyError(f"Unknown stream {stream!r}")
        filters = [TableFilter("run_id", "eq", run_id)]
        if start_ns is not None:
            filters.append(TableFilter(descriptor.time_column, "ge", start_ns))
        if end_ns is not None:
            filters.append(TableFilter(descriptor.time_column, "le", end_ns))
        requested = None if columns is None else list(dict.fromkeys(["run_id", *columns]))
        frame = self.catalog.read_table(
            descriptor.path,
            filters=tuple(filters),
            columns=requested,
            order_by=(descriptor.time_column,),
        ).drop(columns="run_id", errors="ignore")
        if max_points is not None and len(frame) > max_points:
            numeric = [
                name
                for name in frame.select_dtypes(include="number").columns
                if name != descriptor.time_column
            ]
            frame = decimate(frame, numeric[:3], max_points)
        return frame.reset_index(drop=True)
