"""Storage-independent replay queries and local bundle adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from a3docklab.run_metadata import RunMetadata
from a3docklab.telemetry.contracts import BundleManifest, StreamManifest


class ReplayStore(Protocol):
    def list_runs(self) -> list[BundleManifest]: ...

    def query_stream(
        self,
        run_id: str,
        stream: str,
        *,
        start_ns: int | None = None,
        end_ns: int | None = None,
        columns: list[str] | None = None,
        max_points: int | None = None,
    ) -> pd.DataFrame: ...


def decimate(frame: pd.DataFrame, value_columns: list[str], max_points: int) -> pd.DataFrame:
    """Reduce display rows while retaining endpoints and per-bin extrema."""
    if len(frame) <= max_points:
        return frame.reset_index(drop=True)
    bins = max(1, max_points // (2 * max(1, len(value_columns))))
    groups = np.floor(np.arange(len(frame)) * bins / len(frame)).astype(int)
    indexes: set[int] = {0, len(frame) - 1}
    for group in range(bins):
        members = np.flatnonzero(groups == group)
        if not len(members):
            continue
        for column in value_columns:
            values = frame.iloc[members][column].to_numpy(dtype=np.float64)
            indexes.add(int(members[int(np.nanargmin(values))]))
            indexes.add(int(members[int(np.nanargmax(values))]))
    return frame.iloc[sorted(indexes)].reset_index(drop=True)


class LocalReplayStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _manifest(self, directory: Path) -> BundleManifest:
        manifest_path = directory / "manifest.json"
        if manifest_path.exists():
            return BundleManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        metadata = RunMetadata.model_validate_json(
            (directory / "metadata.json").read_text(encoding="utf-8")
        )
        telemetry = pd.read_csv(directory / "telemetry.csv", usecols=["phase"])
        streams = [
            StreamManifest(
                name="truth",
                path="telemetry.csv",
                row_count=len(telemetry),
                format="phase2_csv",
                time_column="event_time_ns",
            )
        ]
        events_path = directory / "events.csv"
        if events_path.exists():
            streams.append(
                StreamManifest(
                    name="events",
                    path="events.csv",
                    row_count=sum(1 for _ in events_path.open(encoding="utf-8")) - 1,
                    format="phase2_events_csv",
                    time_column="event_time_ns",
                )
            )
        return BundleManifest(
            schema_version="1.0",
            run_id=metadata.run_id,
            scenario_id=metadata.scenario,
            created_at_utc=metadata.created_at_utc,
            configuration_hash=metadata.config_sha256,
            terminal_phase=str(telemetry["phase"].iat[-1]),
            streams=streams,
            feature_allowlist_path="",
        )

    def _bundle(self, run_id: str) -> tuple[Path, BundleManifest]:
        directory = self.root / run_id
        manifest = self._manifest(directory)
        if manifest.schema_version.split(".", 1)[0] not in {"1", "2", "3"}:
            raise ValueError(f"Unsupported bundle schema {manifest.schema_version}")
        return directory, manifest

    def list_runs(self) -> list[BundleManifest]:
        return [
            self._manifest(directory)
            for directory in sorted(path.parent for path in self.root.glob("*/metadata.json"))
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
        directory, manifest = self._bundle(run_id)
        descriptor = next((item for item in manifest.streams if item.name == stream), None)
        if descriptor is None:
            raise KeyError(f"Unknown stream {stream!r}")
        path = directory / descriptor.path
        if descriptor.format == "parquet":
            frame = pd.read_parquet(path, columns=columns)
        elif descriptor.format == "jsonl":
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            frame = pd.DataFrame(records)
            if columns is not None:
                frame = frame[columns]
        elif descriptor.format in {"phase2_csv", "phase2_events_csv"}:
            frame = pd.read_csv(path)
            frame["event_time_ns"] = np.rint(
                frame["time_s"].to_numpy(dtype=np.float64) * 1_000_000_000
            ).astype(np.int64)
            if columns is not None:
                frame = frame[columns]
        else:
            raise ValueError(f"Unsupported stream format {descriptor.format}")
        time_column = descriptor.time_column
        if start_ns is not None:
            frame = frame[frame[time_column] >= start_ns]
        if end_ns is not None:
            frame = frame[frame[time_column] <= end_ns]
        if max_points is not None and len(frame) > max_points:
            numeric = [
                column
                for column in frame.select_dtypes(include="number").columns
                if column != time_column
            ]
            frame = decimate(frame, numeric[:3], max_points)
        return frame.reset_index(drop=True)
