from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from a3docklab.config import load_config
from a3docklab.simulation.commands import (
    CommandArbiter,
    ControlIntent,
    DecisionStatus,
    DriverKind,
    IntentMode,
    SimulationObservation,
)
from a3docklab.simulation.engine import SimulationSession


@pytest.fixture(scope="module")
def config():  # type: ignore[no-untyped-def]
    root = Path(__file__).resolve().parents[1]
    return load_config(root / "configs/scenarios/blue_moon_side.yaml")


def _observation(**updates: object) -> SimulationObservation:
    values: dict[str, object] = {
        "run_id": "run-1",
        "time_s": 10.0,
        "phase": "final_approach",
        "position_m": (10.0, 0.0, 0.0),
        "velocity_m_s": (-0.1, 0.0, 0.0),
        "fuel_mass_kg": 1_000.0,
        "closing_rate_m_s": 0.1,
        "closing_rate_limit_m_s": 0.2,
        "corridor_margin_m": 1.0,
        "keep_out_margin_m": 1.0,
        "capture_eligible": False,
    }
    values.update(updates)
    return SimulationObservation.model_validate(values)


def _intent(**updates: object) -> ControlIntent:
    values: dict[str, object] = {
        "command_id": "command-1",
        "driver_id": "operator-1",
        "driver_kind": DriverKind.HUMAN,
        "issued_at_s": 10.0,
        "valid_for_s": 1.0,
        "mode": IntentMode.VELOCITY,
        "desired_velocity_m_s": (-0.1, 0.0, 0.0),
    }
    values.update(updates)
    return ControlIntent.model_validate(values)


def test_velocity_intent_requires_a_finite_vector() -> None:
    with pytest.raises(ValidationError, match="requires desired_velocity"):
        _intent(desired_velocity_m_s=None)
    with pytest.raises(ValidationError, match="finite"):
        _intent(desired_velocity_m_s=(float("nan"), 0.0, 0.0))


def test_arbiter_rejects_wrong_owner_and_future_command(config) -> None:  # type: ignore[no-untyped-def]
    arbiter = CommandArbiter(config, authorized_driver_id="operator-1")
    autopilot = np.array([-0.04, 0.0, 0.0])

    unauthorized = arbiter.decide(
        _observation(), autopilot, _intent(driver_id="operator-2")
    )
    future = arbiter.decide(
        _observation(), autopilot, _intent(issued_at_s=11.0)
    )

    assert unauthorized.status == DecisionStatus.REJECTED
    assert unauthorized.reason == "unauthorized_driver"
    assert unauthorized.executed_velocity_m_s == (0.0, 0.0, 0.0)
    assert future.reason == "command_from_future"


def test_stale_command_falls_back_to_reference_autopilot(config) -> None:  # type: ignore[no-untyped-def]
    autopilot = np.array([-0.04, 0.0, 0.0])
    decision = CommandArbiter(config).decide(
        _observation(time_s=12.0), autopilot, _intent(issued_at_s=10.0)
    )

    assert decision.status == DecisionStatus.SUBSTITUTED
    assert decision.reason == "stale_command_autopilot_fallback"
    assert decision.executed_velocity_m_s == (-0.04, 0.0, 0.0)


def test_arbiter_limits_velocity_closing_rate_and_torque(config) -> None:  # type: ignore[no-untyped-def]
    decision = CommandArbiter(config).decide(
        _observation(closing_rate_limit_m_s=0.2),
        np.zeros(3),
        _intent(
            desired_velocity_m_s=(-2.0, 0.0, 0.0),
            desired_torque_n_m=(1_000.0, -1_000.0, 0.0),
        ),
    )

    assert decision.status == DecisionStatus.LIMITED
    assert "closing_rate_limited" in decision.reason
    assert "torque_limited" in decision.reason
    assert np.linalg.norm(decision.executed_velocity_m_s) <= config.guidance.far_closing_rate_m_s
    assert decision.executed_torque_n_m == (100.0, -100.0, 0.0)


def test_unsafe_closing_command_is_substituted_with_retreat(config) -> None:  # type: ignore[no-untyped-def]
    decision = CommandArbiter(config).decide(
        _observation(corridor_margin_m=-0.1),
        np.zeros(3),
        _intent(desired_velocity_m_s=(-0.1, 0.0, 0.0)),
    )

    assert decision.status == DecisionStatus.SUBSTITUTED
    assert decision.reason == "safety_margin_retreat"
    assert decision.executed_velocity_m_s[0] > 0.0


def test_capture_request_fails_closed_until_gate_is_satisfied(config) -> None:  # type: ignore[no-untyped-def]
    decision = CommandArbiter(config).decide(
        _observation(capture_eligible=False),
        np.zeros(3),
        _intent(mode=IntentMode.CAPTURE, desired_velocity_m_s=None),
    )

    assert decision.status == DecisionStatus.REJECTED
    assert decision.reason == "capture_gate_not_satisfied"


def test_manual_hold_changes_dynamics_and_is_audited(config) -> None:  # type: ignore[no-untyped-def]
    autopilot = SimulationSession(config)
    manual = SimulationSession(config, authorized_driver_id="operator-1")
    hold_0 = _intent(
        command_id="hold-0",
        issued_at_s=0.0,
        mode=IntentMode.HOLD,
        desired_velocity_m_s=None,
    )
    hold_1 = hold_0.model_copy(update={"command_id": "hold-1", "issued_at_s": config.step_s})

    autopilot.step()
    auto_second = autopilot.step()
    first = manual.step(hold_0)
    manual_second = manual.step(hold_1)

    assert first.decision is not None
    assert first.decision.status == DecisionStatus.ACCEPTED
    assert first.state["command_id"] == "hold-0"
    assert first.state["command_driver_kind"] == "human"
    assert manual_second.state["x_m"] != pytest.approx(auto_second.state["x_m"])


def test_checkpoint_replays_manual_command_log(config) -> None:  # type: ignore[no-untyped-def]
    session = SimulationSession(config, authorized_driver_id="operator-1")
    for index in range(4):
        session.step(
            _intent(
                command_id=f"hold-{index}",
                issued_at_s=index * config.step_s,
                mode=IntentMode.HOLD,
                desired_velocity_m_s=None,
            )
        )
    checkpoint = session.checkpoint()
    expected = session.step(
        _intent(
            command_id="hold-4",
            issued_at_s=4 * config.step_s,
            mode=IntentMode.HOLD,
            desired_velocity_m_s=None,
        )
    )

    session.restore(checkpoint)
    actual = session.step(
        _intent(
            command_id="hold-4",
            issued_at_s=4 * config.step_s,
            mode=IntentMode.HOLD,
            desired_velocity_m_s=None,
        )
    )

    assert actual.state == expected.state
    assert actual.decision == expected.decision


def test_operator_abort_transitions_phase_and_emits_event(config) -> None:  # type: ignore[no-untyped-def]
    session = SimulationSession(config, authorized_driver_id="operator-1")
    frame = session.step(
        _intent(
            command_id="abort-now",
            issued_at_s=0.0,
            mode=IntentMode.ABORT,
            desired_velocity_m_s=None,
        )
    )

    assert frame.state["phase"] == "abort"
    assert frame.state["abort_mode"] == "retreat"
    assert frame.state["command_executed_vy_m_s"] != frame.state["command_requested_vy_m_s"]
    event_types = [event["event_type"] for event in frame.events]
    assert event_types[-1] == "operator_abort"
