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

from a3docklab.application.materialization import (
    CompletedSessionArtifact,
    InteractiveSessionManifest,
)
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
        if frame.empty:
            return
        spark_frame = self.spark.createDataFrame(frame)
        spark_frame.write.format("delta").mode("append").saveAsTable(table)

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
            if not frame.empty:
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


class DeltaSessionMaterializer:
    """Append a completed interactive session to normalized Lakehouse tables."""

    def __init__(self, catalog: DeltaCatalog, table_prefix: str = "a3docklab") -> None:
        self.catalog = catalog
        self.table_prefix = table_prefix

    def _table(self, suffix: str) -> str:
        return f"{self.table_prefix}_interactive_{suffix}"

    def materialize(self, artifact: CompletedSessionArtifact) -> InteractiveSessionManifest:
        identity = {
            "session_id": artifact.manifest.session_id,
            "run_id": artifact.manifest.run_id,
            "scenario_id": artifact.manifest.scenario_id,
            "owner": artifact.manifest.owner,
        }
        frames = {
            "telemetry": artifact.telemetry,
            "events": artifact.events,
            "decisions": artifact.decisions,
            "commands": artifact.commands,
            "policy_evaluations": artifact.policy_evaluations,
        }
        for name, source in frames.items():
            if source.empty:
                continue
            frame = source.copy()
            for column, value in identity.items():
                frame[column] = value
            frame = frame[[*identity, *[column for column in frame if column not in identity]]]
            self.catalog.append_table(self._table(name), frame)
        self.catalog.append_table(
            self._table("sessions"),
            pd.DataFrame(
                [
                    {
                        **artifact.manifest.model_dump(mode="json"),
                        "manifest_json": artifact.manifest.model_dump_json(),
                    }
                ]
            ),
        )
        return artifact.manifest


class DeltaReplayStore:
    """ReplayStore implementation querying normalized Delta stream tables."""

    def __init__(self, catalog: DeltaCatalog, table_prefix: str = "a3docklab") -> None:
        self.catalog = catalog
        self.table_prefix = table_prefix
        # Replay re-queries the same run many times as the playback clock advances.
        # Each SQL Warehouse round trip costs seconds, so a full stream is fetched
        # once and cached; per-frame windowing then happens in memory. Without this
        # cache a single frame issues one query per stream and playback stalls.
        self._runs_cache: list[BundleManifest] | None = None
        self._stream_cache: dict[tuple[str, str], pd.DataFrame] = {}

    def _table(self, suffix: str) -> str:
        return f"{self.table_prefix}_{suffix}"

    def list_runs(self) -> list[BundleManifest]:
        if self._runs_cache is None:
            rows = self.catalog.read_table(self._table("runs"), order_by=("created_at_utc",))
            self._runs_cache = [
                BundleManifest.model_validate_json(value)
                for value in rows.get("manifest_json", [])
            ]
        return self._runs_cache

    def _full_stream(self, run_id: str, descriptor: StreamManifest) -> pd.DataFrame:
        key = (run_id, descriptor.name)
        cached = self._stream_cache.get(key)
        if cached is None:
            cached = self.catalog.read_table(
                descriptor.path,
                filters=(TableFilter("run_id", "eq", run_id),),
                order_by=(descriptor.time_column,),
            ).drop(columns="run_id", errors="ignore")
            self._stream_cache[key] = cached
        return cached

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
        if descriptor.row_count == 0:
            return pd.DataFrame(columns=columns)
        time_column = descriptor.time_column
        frame = self._full_stream(run_id, descriptor)
        if start_ns is not None:
            frame = frame[frame[time_column] >= start_ns]
        if end_ns is not None:
            frame = frame[frame[time_column] <= end_ns]
        if columns is not None:
            frame = frame[[column for column in columns if column in frame.columns]]
        if max_points is not None and len(frame) > max_points:
            numeric = [
                name
                for name in frame.select_dtypes(include="number").columns
                if name != time_column
            ]
            frame = decimate(frame, numeric[:3], max_points)
        return frame.reset_index(drop=True)
