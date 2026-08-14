"""Phase 3 local bundle writer."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from a3docklab.run_metadata import RunMetadata
from a3docklab.telemetry.contracts import BundleManifest, StreamManifest
from a3docklab.telemetry.generator import TelemetryStreams


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    try:
        frame.to_parquet(path, index=False)
    except ImportError as exc:
        raise RuntimeError("Phase 3 bundles require the 'storage' optional dependency") from exc


def write_phase3_bundle(
    root: str | Path,
    streams: TelemetryStreams,
    metadata: RunMetadata,
    terminal_phase: str,
) -> BundleManifest:
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    frames = {
        "truth": streams.truth,
        "navigation": streams.navigation,
        "navigation_estimates": streams.navigation_estimates,
        "actuation": streams.actuation,
        "communications": streams.communications,
        "fault_labels": streams.fault_labels,
    }
    manifests: list[StreamManifest] = []
    for name, frame in frames.items():
        path = directory / f"{name}.parquet"
        _write_parquet(frame, path)
        manifests.append(
            StreamManifest(
                name=name,
                path=path.name,
                row_count=len(frame),
                format="parquet",
                time_column="onset_time_ns" if name == "fault_labels" else "event_time_ns",
            )
        )
    events_path = directory / "events.jsonl"
    with events_path.open("w", encoding="utf-8") as stream:
        for record in streams.events.to_dict("records"):
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
    manifests.append(
        StreamManifest(
            name="events",
            path=events_path.name,
            row_count=len(streams.events),
            format="jsonl",
            time_column="event_time_ns",
        )
    )
    allowlist_path = directory / "feature_allowlist.json"
    allowlist_path.write_text(
        json.dumps(list(streams.feature_allowlist), indent=2), encoding="utf-8"
    )
    (directory / "metadata.json").write_text(metadata.model_dump_json(indent=2), encoding="utf-8")
    manifest = BundleManifest(
        schema_version="3.0",
        run_id=metadata.run_id,
        scenario_id=metadata.scenario,
        created_at_utc=metadata.created_at_utc,
        configuration_hash=metadata.config_sha256,
        terminal_phase=terminal_phase,
        streams=manifests,
        feature_allowlist_path=allowlist_path.name,
    )
    (directory / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest
