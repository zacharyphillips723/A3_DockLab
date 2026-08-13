from pathlib import Path

import pytest

from a3docklab.config import SimulationConfig, load_config


def test_example_config_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    assert config.chaser.name == "Orion"
    assert config.docking.docked_controller == "orion"


@pytest.mark.parametrize("scenario", ["blue_moon_side.yaml", "starship_nose.yaml"])
def test_reference_scenarios_validate(scenario: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios" / scenario)
    assert config.step_s <= config.duration_s
    assert all(
        vehicle.assumption_confidence in {"public", "derived", "placeholder"}
        for vehicle in (config.chaser, config.target)
    )


def test_step_cannot_exceed_duration() -> None:
    root = Path(__file__).resolve().parents[1]
    raw = load_config(root / "configs/scenarios/blue_moon_side.yaml").model_dump()
    raw["duration_s"] = 1.0
    raw["step_s"] = 2.0
    with pytest.raises(ValueError, match="step_s must not exceed duration_s"):
        SimulationConfig.model_validate(raw)
