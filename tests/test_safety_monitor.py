from pathlib import Path

import numpy as np

from a3docklab.config import load_config
from a3docklab.dynamics.attitude import rotation_vector_to_quaternion
from a3docklab.safety.monitor import evaluate_safety
from a3docklab.simulation.phases import MissionPhase


def test_excess_closing_rate_commands_braking_abort() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    state = np.array([0.0, -100.0, 0.0, 0.0, 2.0, 0.0])
    status = evaluate_safety(state, MissionPhase.FINAL_APPROACH, config)
    assert status.abort is not None
    assert status.abort.reason == "closing_rate_limit"


def test_corridor_departure_commands_retreat() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    state = np.array([50.0, -100.0, 0.0, 0.0, 0.04, 0.0])
    status = evaluate_safety(state, MissionPhase.FINAL_APPROACH, config)
    assert status.abort is not None
    assert status.abort.reason == "approach_corridor_violation"


def test_terminal_attitude_misalignment_inhibits_capture_and_retreats() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    state = np.array([0.0, -1.0, 0.0, 0.0, 0.04, 0.0])
    misaligned = rotation_vector_to_quaternion(np.array([np.deg2rad(3.0), 0.0, 0.0]))
    status = evaluate_safety(
        state,
        MissionPhase.FINAL_APPROACH,
        config,
        misaligned,
        np.array([1.0, 0.0, 0.0, 0.0]),
    )
    assert not status.capture_eligible
    assert status.abort is not None
    assert status.abort.reason == "docking_angular_misalignment"
