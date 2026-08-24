import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytest

from a3docklab.analysis.contracts import AnalysisBudget
from a3docklab.analysis.risk import LocalRiskSampleMaterializer, LocalRiskStore
from a3docklab.config import load_config
from a3docklab.simulation.monte_carlo import (
    MonteCarloConfig,
    ParameterDistribution,
    run_ensemble,
    write_ensemble,
)
from a3docklab.telemetry.contracts import TelemetryConfig
from a3docklab.visualization.dashboard import _risk_figures
from a3docklab.visualization.replay import LocalReplayStore


def _write_ensemble(root: Path) -> LocalRiskStore:
    directory = root / "ensemble-1"
    directory.mkdir()
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.1",
                "ensemble_id": "ensemble-1",
                "scenario": "blue-moon",
                "sample_count": 4,
                "configuration_sha256": "abc123",
            }
        )
    )
    (directory / "risk_summary.json").write_text(
        json.dumps(
            {
                "sample_count": 4,
                "capture_rate": 0.5,
                "abort_rate": 0.5,
                "p05_closest_approach_m": 0.1,
                "p95_propellant_used_kg": 4.0,
            }
        )
    )
    pd.DataFrame(
        {
            "sample_index": range(4),
            "initial_x_offset_m": [-1.0, 0.0, 1.0, 2.0],
            "capture_success": [True, True, False, False],
            "abort": [False, False, True, True],
            "terminal_phase": ["complete", "complete", "abort", "abort"],
            "sampled_faults": ["[]", '["navigation_drift"]', '["navigation_drift"]', '["stale_data"]'],
            "closest_approach_m": [0.1, 0.2, 1.0, 2.0],
            "propellant_used_kg": [1.0, 2.0, 3.0, 4.0],
            "contact_closing_rate_m_s": [0.01, 0.02, float("nan"), float("nan")],
            "contact_lateral_offset_m": [0.01, 0.02, float("nan"), float("nan")],
            "contact_angular_error_deg": [0.1, 0.2, float("nan"), float("nan")],
        }
    ).to_parquet(directory / "runs.parquet", index=False)
    pd.DataFrame(
        {
            "sample_count": range(1, 5),
            "capture_rate": [1.0, 1.0, 2 / 3, 0.5],
            "abort_rate": [0.0, 0.0, 1 / 3, 0.5],
        }
    ).to_parquet(directory / "convergence.parquet", index=False)
    return LocalRiskStore(root)


def test_local_risk_store_retains_provenance_and_computes_sensitivity(tmp_path: Path) -> None:
    store = _write_ensemble(tmp_path)
    listed = store.list_ensembles()
    result = store.query_ensemble("ensemble-1")

    assert listed[0].configuration_hash == "abc123"
    assert listed[0].source_uri.startswith("file://")
    assert result.rows_scanned == 8
    assert result.sensitivity.iloc[0]["parameter"] == "initial_x_offset_m"
    drift = result.fault_sensitivity.set_index("fault").loc["navigation_drift"]
    assert drift["run_count"] == 2
    assert drift["abort_rate"] == 0.5


def test_risk_query_rejects_work_over_budget(tmp_path: Path) -> None:
    store = _write_ensemble(tmp_path)
    with pytest.raises(ValueError, match="budget permits"):
        store.query_ensemble("ensemble-1", budget=AnalysisBudget(max_rows=7))


def test_risk_figures_preserve_ui_and_bound_convergence_points(tmp_path: Path) -> None:
    result = _write_ensemble(tmp_path).query_ensemble("ensemble-1")
    distributions, convergence, sensitivity = _risk_figures(go, result, point_budget=2)

    assert distributions.layout.uirevision == "risk-distributions-ensemble-1"
    assert len(convergence.data[0].x) == 2
    assert sensitivity.data[0].y[-1] == "initial_x_offset_m"


def test_selected_sample_materializes_as_replay_bundle(tmp_path: Path) -> None:
    scenario = load_config("configs/scenarios/blue_moon_side.yaml")
    scenario.duration_s = 1.0
    config = MonteCarloConfig(
        sample_count=1,
        parameters=(
            ParameterDistribution(
                name="initial_x_offset_m",
                mean=0.0,
                standard_deviation=0.1,
                minimum=-0.2,
                maximum=0.2,
            ),
        ),
        correlation_matrix=((1.0,),),
    )
    telemetry = TelemetryConfig(
        truth_rate_hz=1.0,
        control_rate_hz=1.0,
        navigation_rate_hz=1.0,
        sensor_rate_hz=1.0,
        communications_rate_hz=1.0,
    )
    result = run_ensemble(scenario, config, telemetry)
    ensemble_root = tmp_path / "ensembles"
    write_ensemble(ensemble_root / str(result.manifest["ensemble_id"]), result)
    bundle_root = tmp_path / "bundles"

    status = LocalRiskSampleMaterializer(
        ensemble_root, bundle_root, Path(".")
    ).materialize_sample(str(result.manifest["ensemble_id"]), 0)

    replay_runs = LocalReplayStore(bundle_root).list_runs()
    assert status.state == "completed"
    assert replay_runs[0].run_id == status.run_id
    assert replay_runs[0].configuration_hash
