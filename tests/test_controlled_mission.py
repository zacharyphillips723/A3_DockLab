import math
from pathlib import Path

import pytest

from a3docklab.config import load_config
from a3docklab.simulation.engine import run_controlled, summarize


@pytest.mark.parametrize("scenario", ["blue_moon_side.yaml", "starship_nose.yaml"])
def test_nominal_mission_reaches_capture_without_warnings(scenario: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios" / scenario)
    result = run_controlled(config)
    summary = summarize(result)
    transitions = result.events.query("event_type == 'phase_transition'")["phase"].tolist()

    assert summary.terminal_phase == "complete"
    assert summary.warning_count == 0
    assert summary.closest_approach_m <= config.docking.capture_distance_m
    actual_force = result.telemetry[["actual_fx_n", "actual_fy_n", "actual_fz_n"]]
    assert actual_force.abs().to_numpy().max() <= 1.01 * config.chaser.max_translation_thrust_n
    assert result.telemetry["fuel_mass_kg"].is_monotonic_decreasing
    assert result.telemetry["capture_eligible"].iat[-2]
    assert result.telemetry["port_angular_error_deg"].iat[-2] < config.docking.max_angular_error_deg
    assert (
        abs(result.telemetry["port_clocking_error_deg"].iat[-2])
        < config.docking.max_clocking_error_deg
    )
    assert transitions[-4:] == [
        "final_approach",
        "soft_capture",
        "docked_stack_control",
        "complete",
    ]
    assert result.telemetry["docked_stack_active"].tail(2).all()
    assert result.telemetry["stack_total_mass_kg"].tail(2).notna().all()
    latch_events = result.events.query("event_type == 'capture_latch'")
    assert len(latch_events) == 1
    assert result.telemetry["capture_latched"].tail(2).all()
    assert result.telemetry["capture_linear_momentum_residual_kg_m_s"].iat[-1] < 1e-9
    assert result.telemetry["capture_angular_momentum_residual_kg_m2_s"].iat[-1] < 1e-9
    assert result.telemetry["handoff_state"].iat[-1] == "active"
    assert result.telemetry["controller_authority"].iat[-1] == config.docking.docked_controller
    assert result.telemetry["controller_authority"].notna().all()
    assert result.telemetry["authority_invariant_valid"].all()
    assert (
        result.telemetry["handoff_activation_command_delta"].max()
        <= config.handoff.maximum_command_discontinuity
    )
    stack_control = result.telemetry.query("phase == 'docked_stack_control'")
    assert len(stack_control) >= config.handoff.post_handoff_control_duration_s
    assert stack_control["stack_control_active"].all()
    assert stack_control["stack_controller_vehicle"].iat[-1] == config.docking.docked_controller
    if config.docking.docked_controller == "orion":
        assert (
            stack_control["chaser_propellant_used_kg"].iat[-1]
            > stack_control["chaser_propellant_used_kg"].iat[0]
        )
    else:
        assert (
            stack_control["target_propellant_used_kg"].iat[-1]
            > stack_control["target_propellant_used_kg"].iat[0]
        )


def test_unsafe_initial_closing_rate_aborts_deterministically() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    config.initial_relative_state_m_mps = (0.0, -1000.0, 0.0, 0.0, 2.0, 0.0)

    first = run_controlled(config)
    second = run_controlled(config)

    assert summarize(first).terminal_phase == "abort"
    abort_events = first.events.query("event_type == 'abort'")
    assert abort_events.iloc[-1]["detail"] == "braking:closing_rate_limit"
    assert first.events.iloc[-1]["event_type"] == "abort_response_complete"
    assert first.telemetry["closing_rate_m_s"].iat[-1] <= 0.02
    assert first.events.to_dict("records") == second.events.to_dict("records")


def test_persistent_terminal_misalignment_commands_retreat() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    angle_rad = math.radians(5.0)
    config.attitude.chaser_initial_quaternion_wxyz = (
        math.cos(angle_rad / 2.0),
        math.sin(angle_rad / 2.0),
        0.0,
        0.0,
    )
    config.attitude.control_enabled = False

    result = run_controlled(config)
    assert summarize(result).terminal_phase == "abort"
    aborts = result.events.query("event_type == 'abort'")
    assert aborts.iloc[-1]["detail"] == "retreat:docking_angular_misalignment"
    assert result.telemetry["port_angular_error_deg"].max() == pytest.approx(5.0, abs=0.01)


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("stale_data", "readiness_lost"),
        ("frame_mismatch", "readiness_lost"),
        ("actuator_unhealthy", "readiness_lost"),
        ("lost_acknowledgement", "acknowledgement_timeout"),
        ("duplicated_authority", "authority_invariant_violation"),
        ("lost_authority", "authority_invariant_violation"),
        ("shadow_command_mismatch", "shadow_command_mismatch"),
    ],
)
def test_handoff_fault_rolls_back_with_orion_authority(fault: str, reason: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/starship_nose.yaml")
    config.handoff.injected_fault = fault  # type: ignore[assignment]
    result = run_controlled(config)
    assert summarize(result).terminal_phase == "complete"
    assert result.telemetry["handoff_state"].iat[-1] == "rollback"
    assert result.telemetry["controller_authority"].iat[-1] == "orion"
    assert reason in result.events.query("event_type == 'handoff_state'")["detail"].iat[-1]
    if fault in {"duplicated_authority", "lost_authority"}:
        assert not result.telemetry["authority_invariant_valid"].all()
        assert result.telemetry["authority_invariant_valid"].iat[-1]


def test_active_target_failure_rolls_back_and_continues_orion_stack_control() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/starship_nose.yaml")
    config.handoff.injected_fault = "active_owner_failure"
    result = run_controlled(config)
    stack_control = result.telemetry.query("phase == 'docked_stack_control'")
    assert summarize(result).terminal_phase == "complete"
    assert "target" in stack_control["stack_controller_vehicle"].values
    assert stack_control["stack_controller_vehicle"].iat[-1] == "orion"
    assert result.telemetry["handoff_state"].iat[-1] == "rollback"
    assert result.telemetry["active_owner_failure_injected"].iat[-1]
