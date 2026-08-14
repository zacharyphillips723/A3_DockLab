from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from a3docklab.config import load_config
from a3docklab.faults.models import FaultWindow
from a3docklab.platform.delta import DeltaReplayStore, DeltaRunStorage, InMemoryDeltaCatalog
from a3docklab.run_metadata import build_run_metadata, load_assumptions, load_source_revision
from a3docklab.simulation.engine import SimulationResult, run_controlled, summarize
from a3docklab.telemetry.bundle import write_phase3_bundle
from a3docklab.telemetry.contracts import FaultConfig, TelemetryConfig
from a3docklab.telemetry.generator import generate_streams
from a3docklab.visualization.dashboard import create_app
from a3docklab.visualization.replay import LocalReplayStore


@pytest.fixture(scope="module")
def controlled_result() -> tuple[Path, object, SimulationResult]:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    return root, config, run_controlled(config)


def test_multirate_stream_counts_and_seed_reproducibility(controlled_result: tuple) -> None:
    _, config, result = controlled_result
    telemetry = TelemetryConfig()
    first = generate_streams(result, telemetry, FaultConfig(), random_seed=config.random_seed)
    second = generate_streams(result, telemetry, FaultConfig(), random_seed=config.random_seed)
    end_s = float(result.telemetry["time_s"].iat[-1])

    assert len(first.truth) == int(end_s * telemetry.truth_rate_hz) + 1
    assert len(first.navigation) == int(end_s * telemetry.relative_sensor_rate_hz) + 1
    assert len(first.navigation_estimates) == int(end_s * telemetry.navigation_rate_hz) + 1
    estimates = first.navigation_estimates
    assert (estimates["covariance_trace"] >= 0.0).all()
    measured = estimates.query("measurement_used")
    assert len(measured) == len(first.navigation)
    assert (measured["normalized_innovation_squared"] >= 0.0).all()
    assert len(first.communications) == int(end_s * telemetry.communications_rate_hz) + 1
    pd.testing.assert_frame_equal(first.navigation, second.navigation)
    assert "fault_active" not in first.feature_allowlist
    sensor_times = first.navigation["event_time_ns"].to_numpy(dtype=np.int64) / 1_000_000_000
    truth_x = np.interp(
        sensor_times,
        result.telemetry["time_s"].to_numpy(dtype=np.float64),
        result.telemetry["x_m"].to_numpy(dtype=np.float64),
    )
    x_error = first.navigation["nav_x_m"].to_numpy(dtype=np.float64) - truth_x
    assert abs(float(x_error.mean())) < 0.005
    assert float(x_error.std()) == pytest.approx(telemetry.position_noise_sigma_m, rel=0.1)


def test_faults_change_observations_without_rewriting_source_time(controlled_result: tuple) -> None:
    _, config, result = controlled_result
    faults = FaultConfig(
        faults=[
            FaultWindow(
                name="navigation_drift",
                start_s=100.0,
                duration_s=20.0,
                severity=0.1,
                affected_channels=("nav_y_m",),
            ),
            FaultWindow(
                name="thruster_underperformance",
                start_s=100.0,
                duration_s=20.0,
                severity=0.5,
                affected_channels=("actual_fy_n",),
            ),
            FaultWindow(
                name="communications_latency",
                start_s=100.0,
                duration_s=20.0,
                severity=1.5,
                affected_channels=("receive_time_ns",),
            ),
        ]
    )
    streams = generate_streams(result, TelemetryConfig(), faults, random_seed=config.random_seed)

    assert streams.navigation.loc[streams.navigation["fault_active"], "nav_y_m"].notna().all()
    assert (
        streams.actuation.loc[streams.actuation["fault_active"], "command_response_residual_n"]
        .gt(0.0)
        .any()
    )
    affected = streams.communications[streams.communications["fault_active"]]
    assert affected["data_age_ns"].min() >= 1_600_000_000
    assert (affected["receive_time_ns"] >= affected["event_time_ns"]).all()
    assert len(streams.fault_labels) == 3


def test_bundle_replay_queries_and_dashboard_smoke(
    tmp_path: Path, controlled_result: tuple
) -> None:
    root, config, result = controlled_result
    metadata = build_run_metadata(
        config,
        load_source_revision(root / "docs/mission_facts.yaml"),
        load_assumptions(root / "docs/assumption_register.csv"),
        created_at_utc=datetime(2026, 8, 4, tzinfo=UTC),
    )
    streams = generate_streams(
        result, TelemetryConfig(), FaultConfig(), random_seed=config.random_seed
    )
    directory = tmp_path / metadata.run_id
    write_phase3_bundle(directory, streams, metadata, summarize(result).terminal_phase)

    store = LocalReplayStore(tmp_path)
    assert [run.run_id for run in store.list_runs()] == [metadata.run_id]
    window = store.query_stream(
        metadata.run_id,
        "truth",
        start_ns=100_000_000_000,
        end_ns=200_000_000_000,
        max_points=100,
    )
    assert len(window) <= 100
    assert window["event_time_ns"].min() >= 100_000_000_000
    app = create_app(store)
    assert app.title == "A3 DockLab Mission Replay"


def test_phase2_bundle_compatibility(tmp_path: Path, controlled_result: tuple) -> None:
    root, config, result = controlled_result
    metadata = build_run_metadata(
        config,
        load_source_revision(root / "docs/mission_facts.yaml"),
        load_assumptions(root / "docs/assumption_register.csv"),
        created_at_utc=datetime(2026, 8, 4, tzinfo=UTC),
    )
    directory = tmp_path / metadata.run_id
    directory.mkdir()
    result.telemetry.to_csv(directory / "telemetry.csv", index=False)
    assert result.events is not None
    result.events.to_csv(directory / "events.csv", index=False)
    (directory / "metadata.json").write_text(metadata.model_dump_json(), encoding="utf-8")

    store = LocalReplayStore(tmp_path)
    truth = store.query_stream(metadata.run_id, "truth", max_points=50)
    assert "event_time_ns" in truth
    assert len(truth) <= 50


def test_delta_storage_and_replay_share_the_local_contract(controlled_result: tuple) -> None:
    root, config, result = controlled_result
    metadata = build_run_metadata(
        config,
        load_source_revision(root / "docs/mission_facts.yaml"),
        load_assumptions(root / "docs/assumption_register.csv"),
        created_at_utc=datetime(2026, 8, 4, tzinfo=UTC),
    )
    streams = generate_streams(
        result, TelemetryConfig(), FaultConfig(), random_seed=config.random_seed
    )
    catalog = InMemoryDeltaCatalog()
    manifest = DeltaRunStorage(catalog, "test_a3").write_bundle(
        streams, metadata, summarize(result).terminal_phase
    )
    store = DeltaReplayStore(catalog, "test_a3")

    assert store.list_runs() == [manifest]
    window = store.query_stream(
        metadata.run_id,
        "truth",
        start_ns=100_000_000_000,
        end_ns=200_000_000_000,
        columns=["event_time_ns", "x_m"],
        max_points=100,
    )
    assert list(window.columns) == ["event_time_ns", "x_m"]
    assert len(window) <= 100
    assert window["event_time_ns"].between(100_000_000_000, 200_000_000_000).all()
    assert "test_a3_navigation_estimates" in catalog.tables
