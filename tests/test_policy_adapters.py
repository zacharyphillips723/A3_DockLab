from pathlib import Path

import pandas as pd

from a3docklab.config import load_config
from a3docklab.simulation.engine import SimulationSession, run_controlled
from a3docklab.simulation.policies import ReferenceAutopilotPolicy


def scenario():
    path = Path(__file__).resolve().parents[1] / "configs/scenarios/blue_moon_side.yaml"
    return load_config(path)


def test_reference_policy_uses_versioned_guarded_contract() -> None:
    session = SimulationSession(scenario(), active_policy=ReferenceAutopilotPolicy())
    frame = session.step()

    assert frame.decision is not None
    assert frame.decision.driver_id == "reference-autopilot"
    assert frame.decision.requested_mode == "autopilot"
    assert frame.decision.status == "accepted"
    assert frame.state["command_driver_id"] == "reference-autopilot"


def test_reference_active_policy_preserves_reference_physics() -> None:
    config = scenario()
    expected = run_controlled(config).telemetry
    actual = SimulationSession(config, active_policy=ReferenceAutopilotPolicy()).run_to_completion()
    physics_columns = [column for column in expected.columns if not column.startswith("command_")]

    pd.testing.assert_frame_equal(expected[physics_columns], actual.telemetry[physics_columns])


def test_shadow_policy_is_observable_and_has_no_control_authority() -> None:
    config = scenario()
    expected = run_controlled(config)
    session = SimulationSession(config, shadow_policy=ReferenceAutopilotPolicy())
    first = session.step()
    actual = session.run_to_completion()

    assert first.shadow_policy is not None
    assert first.shadow_policy.policy_id == "reference-autopilot"
    assert first.shadow_decision is not None
    assert first.shadow_decision.status == "accepted"
    pd.testing.assert_frame_equal(expected.telemetry, actual.telemetry)
    pd.testing.assert_frame_equal(expected.events, actual.events)
