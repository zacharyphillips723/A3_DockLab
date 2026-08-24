import numpy as np
import pandas as pd

from a3docklab.config import load_config
from a3docklab.simulation.monte_carlo import (
    FaultDistribution,
    MonteCarloConfig,
    ParameterDistribution,
    run_ensemble,
    sample_parameters,
)
from a3docklab.telemetry.contracts import TelemetryConfig


def _config(sample_count: int = 100) -> MonteCarloConfig:
    return MonteCarloConfig(
        sample_count=sample_count,
        random_seed=1234,
        parameters=(
            ParameterDistribution(
                name="chaser_mass_scale",
                mean=1.0,
                standard_deviation=0.05,
                minimum=0.8,
                maximum=1.2,
            ),
            ParameterDistribution(
                name="chaser_thrust_scale",
                mean=1.0,
                standard_deviation=0.05,
                minimum=0.8,
                maximum=1.2,
            ),
        ),
        correlation_matrix=((1.0, -0.5), (-0.5, 1.0)),
    )


def test_sampling_is_reproducible_bounded_and_correlated() -> None:
    config = _config(5000)
    first = sample_parameters(config)
    second = sample_parameters(config)
    pd.testing.assert_frame_equal(first, second)
    assert first["chaser_mass_scale"].between(0.8, 1.2).all()
    correlation = np.corrcoef(first["chaser_mass_scale"], first["chaser_thrust_scale"])[0, 1]
    assert correlation < -0.4


def test_small_ensemble_has_manifest_metrics_and_convergence() -> None:
    scenario = load_config("configs/scenarios/blue_moon_side.yaml")
    scenario.duration_s = 3.0
    result = run_ensemble(scenario, _config(3))
    assert len(result.runs) == 3
    assert len(result.convergence) == 3
    assert result.manifest["sample_count"] == 3
    assert result.manifest["base_configuration"]["duration_s"] == 3.0
    assert result.risk_summary["sample_count"] == 3
    assert {
        "run_id",
        "contact_closing_rate_m_s",
        "contact_lateral_offset_m",
        "contact_angular_error_deg",
        "capture_dissipated_energy_j",
    } <= set(result.runs.columns)


def test_fault_sampling_and_estimator_metrics_are_reproducible() -> None:
    scenario = load_config("configs/scenarios/blue_moon_side.yaml")
    scenario.duration_s = 3.0
    config = _config(1).model_copy(
        update={
            "faults": (
                FaultDistribution(
                    name="navigation_drift",
                    probability=1.0,
                    severity=0.001,
                    start_fraction=0.25,
                    duration_fraction=0.5,
                ),
            )
        }
    )
    telemetry = TelemetryConfig(
        truth_rate_hz=1.0,
        control_rate_hz=1.0,
        navigation_rate_hz=2.0,
        sensor_rate_hz=1.0,
        communications_rate_hz=1.0,
    )
    result = run_ensemble(scenario, config, telemetry)
    assert result.runs.loc[0, "sampled_faults"] == '["navigation_drift"]'
    assert bool(result.runs.loc[0, "faulted"])
    assert result.runs.loc[0, "estimator_position_rmse_m"] >= 0.0
    assert 0.0 <= result.runs.loc[0, "nis_consistent_fraction"] <= 1.0
    assert "p95_estimator_position_rmse_m" in result.risk_summary
