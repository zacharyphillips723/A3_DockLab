from pathlib import Path

from a3docklab.config import load_config
from a3docklab.run_metadata import deterministic_run_id
from a3docklab.simulation.engine import run_cw


def test_reference_run_has_stable_identity_and_expected_samples() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/starship_nose.yaml")
    result = run_cw(config)

    assert len(result.telemetry) == int(config.duration_s / config.step_s) + 1
    assert result.telemetry["run_id"].nunique() == 1
    assert result.telemetry["run_id"].iat[0] == deterministic_run_id(config)
    assert result.telemetry["random_seed"].iat[0] == config.random_seed
    assert result.telemetry["range_m"].ge(0.0).all()
