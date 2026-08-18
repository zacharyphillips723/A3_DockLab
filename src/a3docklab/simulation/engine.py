"""Minimal deterministic simulation engine for the starter repository."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass

import numpy as np
import pandas as pd

from a3docklab.config import SimulationConfig
from a3docklab.control.allocation import allocate_wrench, default_thruster_layout
from a3docklab.control.attitude import attitude_pd_torque
from a3docklab.control.handoff import (
    AuthorityHandoff,
    AuthorityOwner,
    ExchangeValidation,
    HandoffExchangePacket,
    HandoffInputs,
    HandoffState,
    ShadowWrench,
    authority_observation_valid,
    command_delta_norm,
    shadow_stack_wrench,
    validate_exchange_packet,
)
from a3docklab.control.translation import commanded_acceleration
from a3docklab.dynamics.attitude import (
    normalize_quaternion,
    quaternion_to_dcm,
    rigid_body_derivative,
)
from a3docklab.dynamics.combined_vehicle import RigidBodyProperties, docked_stack_properties
from a3docklab.dynamics.cw import derivative, mean_motion, propagate
from a3docklab.dynamics.docking_contact import CaptureLatchResult, latch_capture
from a3docklab.guidance.profiles import axis_index, commanded_velocity
from a3docklab.run_metadata import deterministic_run_id
from a3docklab.safety.monitor import evaluate_safety
from a3docklab.simulation.phases import MissionPhase, PhaseMachine


@dataclass(frozen=True)
class SimulationResult:
    telemetry: pd.DataFrame
    events: pd.DataFrame | None = None


@dataclass(frozen=True)
class MissionSummary:
    terminal_phase: str
    elapsed_time_s: float
    propellant_used_kg: float
    closest_approach_m: float
    warning_count: int


@dataclass(frozen=True)
class SimulationFrame:
    """One observable integration frame and the events emitted during it."""

    state: dict[str, object]
    events: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class SimulationCheckpoint:
    """Portable deterministic restore point for a controlled session."""

    run_id: str
    step_index: int
    time_s: float
    state: dict[str, object]


def run_cw(config: SimulationConfig) -> SimulationResult:
    """Run an uncontrolled CW reference trajectory and emit truth telemetry."""
    n = mean_motion(config.orbit.earth_mu_m3_s2, config.orbit.semi_major_axis_m)
    initial = np.asarray(config.initial_relative_state_m_mps, dtype=np.float64)
    times = np.arange(0.0, config.duration_s + 0.5 * config.step_s, config.step_s)
    states = np.vstack([propagate(initial, n, float(time_s)) for time_s in times])
    range_m = np.linalg.norm(states[:, :3], axis=1)
    closing_rate_m_s = np.zeros_like(range_m)
    nonzero = range_m > 1e-12
    closing_rate_m_s[nonzero] = (
        -np.sum(states[nonzero, :3] * states[nonzero, 3:], axis=1) / range_m[nonzero]
    )

    telemetry = pd.DataFrame(
        {
            "run_id": deterministic_run_id(config),
            "time_s": times,
            "x_m": states[:, 0],
            "y_m": states[:, 1],
            "z_m": states[:, 2],
            "vx_m_s": states[:, 3],
            "vy_m_s": states[:, 4],
            "vz_m_s": states[:, 5],
            "range_m": range_m,
            "closing_rate_m_s": closing_rate_m_s,
            "scenario": config.name,
            "random_seed": config.random_seed,
            "fidelity": config.fidelity,
            "phase": "reference_propagation",
        }
    )
    return SimulationResult(telemetry=telemetry)


def _rk4_step(
    state: np.ndarray, acceleration_m_s2: np.ndarray, n_rad_s: float, step_s: float
) -> np.ndarray:
    def dynamics(value: np.ndarray) -> np.ndarray:
        return derivative(value, n_rad_s, acceleration_m_s2)

    k1 = dynamics(state)
    k2 = dynamics(state + 0.5 * step_s * k1)
    k3 = dynamics(state + 0.5 * step_s * k2)
    k4 = dynamics(state + step_s * k3)
    return np.asarray(state + step_s * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0, dtype=np.float64)


def _attitude_rk4_step(
    state: np.ndarray, inertia_kg_m2: np.ndarray, torque_body_n_m: np.ndarray, step_s: float
) -> np.ndarray:
    def dynamics(value: np.ndarray) -> np.ndarray:
        return rigid_body_derivative(0.0, value, inertia_kg_m2, torque_body_n_m)

    k1 = dynamics(state)
    k2 = dynamics(state + 0.5 * step_s * k1)
    k3 = dynamics(state + 0.5 * step_s * k2)
    k4 = dynamics(state + step_s * k3)
    result = np.asarray(state + step_s * (k1 + 2 * k2 + 2 * k3 + k4) / 6.0)
    result[:4] = normalize_quaternion(result[:4])
    return result


def _controlled_frames(config: SimulationConfig) -> Iterator[SimulationFrame]:
    """Yield a phase-controlled mission one observable integration frame at a time."""
    if config.fidelity != "cw":
        raise ValueError("controlled simulation currently requires fidelity='cw'")
    n_rad_s = mean_motion(config.orbit.earth_mu_m3_s2, config.orbit.semi_major_axis_m)
    state = np.asarray(config.initial_relative_state_m_mps, dtype=np.float64)
    machine = PhaseMachine(config.guidance)
    fuel_mass_kg = config.chaser.mass_kg
    initial_fuel_mass_kg = fuel_mass_kg
    target_fuel_mass_kg = config.target.mass_kg
    initial_target_fuel_mass_kg = target_fuel_mass_kg
    events: list[dict[str, object]] = []
    run_id = deterministic_run_id(config)
    abort_mode = ""
    chaser_attitude = np.asarray(
        [
            *config.attitude.chaser_initial_quaternion_wxyz,
            *config.attitude.chaser_initial_rate_rad_s,
        ],
        dtype=np.float64,
    )
    target_attitude = np.asarray(
        [*config.attitude.target_quaternion_wxyz, *config.attitude.target_rate_rad_s],
        dtype=np.float64,
    )
    chaser_inertia = np.asarray(config.chaser.inertia_kg_m2, dtype=np.float64)
    target_inertia = np.asarray(config.target.inertia_kg_m2, dtype=np.float64)
    chaser_thrusters = default_thruster_layout(config.chaser)
    target_thrusters = default_thruster_layout(config.target)
    pending_chaser_duties = np.zeros(len(chaser_thrusters), dtype=np.float64)
    pending_target_duties = np.zeros(len(target_thrusters), dtype=np.float64)
    stack = docked_stack_properties(config)
    capture_result: CaptureLatchResult | None = None
    desired_owner = (
        AuthorityOwner.ORION
        if config.docking.docked_controller == "orion"
        else AuthorityOwner.TARGET
    )
    handoff = AuthorityHandoff(desired_owner, config.handoff)
    handoff_reason = "not_started"
    exchange_validation: ExchangeValidation | None = None
    exchange_sequence = 0
    exchange_frame_id = config.handoff.required_frame_id
    zero_shadow = ShadowWrench(np.zeros(3), np.zeros(3))
    orion_shadow = zero_shadow
    target_shadow = zero_shadow
    shadow_candidate_delta = 0.0
    activation_command_delta = 0.0
    observed_orion_authority = True
    observed_target_authority = False
    authority_invariant_valid = True
    stack_velocity = np.zeros(3, dtype=np.float64)
    stack_angular_rate = np.zeros(3, dtype=np.float64)
    active_owner_failure_injected = False

    for time_s in np.arange(0.0, config.duration_s + 0.5 * config.step_s, config.step_s):
        event_start = len(events)
        if handoff.state == HandoffState.ROLLBACK:
            observed_orion_authority = True
            observed_target_authority = False
            authority_invariant_valid = True
        range_m = float(np.linalg.norm(state[:3]))
        docking_axis = axis_index(config.guidance.docking_axis)
        safety = evaluate_safety(
            state, machine.phase, config, chaser_attitude[:4], target_attitude[:4]
        )
        previous_phase = machine.phase
        changed = machine.advance(
            range_m,
            float(time_s),
            config.docking.capture_distance_m,
            abs(float(state[3 + docking_axis])),
            safety.capture_eligible,
            handoff.state in {HandoffState.ACTIVE, HandoffState.ROLLBACK},
            config.handoff.post_handoff_control_duration_s,
        )
        if changed:
            events.append(
                {
                    "run_id": run_id,
                    "time_s": float(time_s),
                    "event_type": "phase_transition",
                    "phase": machine.phase.value,
                    "detail": f"{previous_phase.value}->{machine.phase.value}",
                }
            )
        elif capture_result is not None and handoff.state not in {
            HandoffState.ACTIVE,
            HandoffState.ROLLBACK,
        }:
            fault = config.handoff.injected_fault
            orion_shadow = shadow_stack_wrench(
                capture_result.combined_velocity_reference_m_s,
                capture_result.combined_angular_rate_reference_rad_s,
                stack.total_mass_kg,
                stack.inertia_about_com_reference_kg_m2,
                config.chaser,
                config.handoff,
            )
            target_shadow = shadow_stack_wrench(
                capture_result.combined_velocity_reference_m_s,
                capture_result.combined_angular_rate_reference_rad_s,
                stack.total_mass_kg,
                stack.inertia_about_com_reference_kg_m2,
                config.target,
                config.handoff,
            )
            lever_scale = max(config.chaser.diameter_m, config.target.diameter_m)
            shadow_candidate_delta = command_delta_norm(orion_shadow, target_shadow, lever_scale)
            selected_shadow = (
                orion_shadow if handoff.desired_owner == AuthorityOwner.ORION else target_shadow
            )
            activation_command_delta = command_delta_norm(zero_shadow, selected_shadow, lever_scale)
            if fault == "shadow_command_mismatch":
                shadow_candidate_delta = config.handoff.maximum_command_discontinuity + 1.0
            observed_orion_authority = handoff.owner == AuthorityOwner.ORION
            observed_target_authority = handoff.owner == AuthorityOwner.TARGET
            if fault == "duplicated_authority":
                observed_orion_authority = True
                observed_target_authority = True
            elif fault == "lost_authority":
                observed_orion_authority = False
                observed_target_authority = False
            authority_invariant_valid = authority_observation_valid(
                observed_orion_authority, observed_target_authority
            )
            source_time_s = (
                float(time_s) - config.handoff.maximum_data_age_s - 1.0
                if fault == "stale_data"
                else float(time_s)
            )
            exchange_frame_id = (
                "chaser_body" if fault == "frame_mismatch" else config.handoff.required_frame_id
            )
            packet = HandoffExchangePacket(
                schema_version="2.0",
                sequence_number=exchange_sequence,
                source_time_s=source_time_s,
                frame_id=exchange_frame_id,
                mission_phase=machine.phase.value,
                position_m=(float(state[0]), float(state[1]), float(state[2])),
                velocity_m_s=(float(state[3]), float(state[4]), float(state[5])),
                attitude_quaternion_wxyz=(
                    float(chaser_attitude[0]),
                    float(chaser_attitude[1]),
                    float(chaser_attitude[2]),
                    float(chaser_attitude[3]),
                ),
                angular_rate_rad_s=(
                    float(chaser_attitude[4]),
                    float(chaser_attitude[5]),
                    float(chaser_attitude[6]),
                ),
                covariance=np.eye(12) * 1e-4,
                actuator_health_fraction=0.5 if fault == "actuator_unhealthy" else 1.0,
            )
            exchange_sequence += 1
            exchange_validation = validate_exchange_packet(
                packet,
                receive_time_s=float(time_s),
                expected_phase=machine.phase.value,
                config=config.handoff,
            )
            update = handoff.advance(
                float(time_s),
                HandoffInputs(
                    state_valid=exchange_validation.state_valid,
                    covariance_valid=exchange_validation.covariance_valid,
                    clock_synchronized=exchange_validation.clock_synchronized,
                    phase_consistent=exchange_validation.phase_consistent,
                    actuator_healthy=exchange_validation.actuator_healthy,
                    acknowledgement_received=fault != "lost_acknowledgement",
                    authority_consistent=authority_invariant_valid,
                    shadow_command_delta=max(shadow_candidate_delta, activation_command_delta),
                ),
            )
            handoff_reason = update.reason
            if update.changed:
                events.append(
                    {
                        "run_id": run_id,
                        "time_s": float(time_s),
                        "event_type": "handoff_state",
                        "phase": machine.phase.value,
                        "detail": (
                            f"{update.previous_state.value}->{update.state.value}:{update.reason}"
                        ),
                    }
                )
        if changed and machine.phase == MissionPhase.SOFT_CAPTURE:
            chaser_rotation = quaternion_to_dcm(chaser_attitude[:4])
            target_rotation = quaternion_to_dcm(target_attitude[:4])
            capture_result = latch_capture(
                RigidBodyProperties(
                    config.chaser.mass_kg,
                    stack.chaser_center_reference_m,
                    chaser_rotation @ np.diag(config.chaser.inertia_kg_m2) @ chaser_rotation.T,
                ),
                RigidBodyProperties(
                    config.target.mass_kg,
                    stack.target_center_reference_m,
                    target_rotation @ np.diag(config.target.inertia_kg_m2) @ target_rotation.T,
                ),
                state[3:],
                np.zeros(3),
                chaser_rotation @ chaser_attitude[4:],
                target_rotation @ target_attitude[4:],
                stack,
            )
            stack_velocity = capture_result.combined_velocity_reference_m_s.copy()
            stack_angular_rate = capture_result.combined_angular_rate_reference_rad_s.copy()
            events.append(
                {
                    "run_id": run_id,
                    "time_s": float(time_s),
                    "event_type": "capture_latch",
                    "phase": machine.phase.value,
                    "detail": (
                        f"dissipated_energy_j={capture_result.dissipated_energy_j:.9g};"
                        f"linear_residual={capture_result.linear_momentum_residual_kg_m_s:.3e};"
                        f"angular_residual={capture_result.angular_momentum_residual_kg_m2_s:.3e}"
                    ),
                }
            )
            update = handoff.begin(float(time_s))
            handoff_reason = update.reason
            events.append(
                {
                    "run_id": run_id,
                    "time_s": float(time_s),
                    "event_type": "handoff_state",
                    "phase": machine.phase.value,
                    "detail": f"{update.previous_state.value}->{update.state.value}:{update.reason}",
                }
            )
            orion_shadow = shadow_stack_wrench(
                capture_result.combined_velocity_reference_m_s,
                capture_result.combined_angular_rate_reference_rad_s,
                stack.total_mass_kg,
                stack.inertia_about_com_reference_kg_m2,
                config.chaser,
                config.handoff,
            )
            target_shadow = shadow_stack_wrench(
                capture_result.combined_velocity_reference_m_s,
                capture_result.combined_angular_rate_reference_rad_s,
                stack.total_mass_kg,
                stack.inertia_about_com_reference_kg_m2,
                config.target,
                config.handoff,
            )

        if (
            capture_result is not None
            and machine.phase == MissionPhase.DOCKED_STACK_CONTROL
            and handoff.state == HandoffState.ACTIVE
            and config.handoff.injected_fault == "active_owner_failure"
            and not active_owner_failure_injected
            and float(time_s) - machine.entered_at_s >= config.handoff.active_failure_delay_s
        ):
            update = handoff.fail_active_owner(float(time_s))
            handoff_reason = update.reason
            active_owner_failure_injected = True
            observed_orion_authority = True
            observed_target_authority = False
            authority_invariant_valid = True
            events.append(
                {
                    "run_id": run_id,
                    "time_s": float(time_s),
                    "event_type": "handoff_state",
                    "phase": machine.phase.value,
                    "detail": f"{update.previous_state.value}->{update.state.value}:{update.reason}",
                }
            )

        if changed:
            safety = evaluate_safety(
                state, machine.phase, config, chaser_attitude[:4], target_attitude[:4]
            )
        if safety.abort is not None and machine.phase != MissionPhase.ABORT:
            machine.transition(MissionPhase.ABORT, float(time_s))
            abort_mode = safety.abort.mode.value
            events.append(
                {
                    "run_id": run_id,
                    "time_s": float(time_s),
                    "event_type": "abort",
                    "phase": machine.phase.value,
                    "detail": f"{safety.abort.mode.value}:{safety.abort.reason}",
                }
            )

        desired_velocity = commanded_velocity(
            state[:3], machine.phase, config.guidance, config.safety, abort_mode
        )
        requested_acceleration = commanded_acceleration(
            state, desired_velocity, n_rad_s, config.controller
        )
        if capture_result is not None and handoff.state != HandoffState.ACTIVE:
            requested_acceleration = np.zeros(3)
        attitude_torque_n_m = (
            attitude_pd_torque(
                chaser_attitude[:4],
                target_attitude[:4],
                chaser_attitude[4:],
                target_attitude[4:],
                np.asarray(config.attitude.proportional_gain_n_m_per_rad),
                np.asarray(config.attitude.derivative_gain_n_m_s_per_rad),
                np.asarray(config.attitude.maximum_torque_n_m),
            )
            if config.attitude.control_enabled
            else np.zeros(3)
        )
        if capture_result is not None and handoff.state != HandoffState.ACTIVE:
            attitude_torque_n_m = np.zeros(3)
        if capture_result is not None:
            orion_shadow = shadow_stack_wrench(
                stack_velocity,
                stack_angular_rate,
                stack.total_mass_kg,
                stack.inertia_about_com_reference_kg_m2,
                config.chaser,
                config.handoff,
            )
            target_shadow = shadow_stack_wrench(
                stack_velocity,
                stack_angular_rate,
                stack.total_mass_kg,
                stack.inertia_about_com_reference_kg_m2,
                config.target,
                config.handoff,
            )
        stack_control_active = (
            capture_result is not None and machine.phase == MissionPhase.DOCKED_STACK_CONTROL
        )
        if stack_control_active:
            selected_shadow = (
                orion_shadow if handoff.owner == AuthorityOwner.ORION else target_shadow
            )
            controller_vehicle = (
                config.chaser if handoff.owner == AuthorityOwner.ORION else config.target
            )
            controller_thrusters = (
                chaser_thrusters if handoff.owner == AuthorityOwner.ORION else target_thrusters
            )
            controller_pending = (
                pending_chaser_duties
                if handoff.owner == AuthorityOwner.ORION
                else pending_target_duties
            )
            requested_force_lvlh_n = selected_shadow.force_reference_n
            requested_force_body_n = requested_force_lvlh_n
            attitude_torque_n_m = selected_shadow.torque_reference_n_m
        else:
            controller_vehicle = config.chaser
            controller_thrusters = chaser_thrusters
            controller_pending = pending_chaser_duties
            body_to_lvlh = quaternion_to_dcm(chaser_attitude[:4])
            requested_force_lvlh_n = requested_acceleration * config.chaser.mass_kg
            requested_force_body_n = body_to_lvlh.T @ requested_force_lvlh_n
        allocation = allocate_wrench(
            requested_force_body_n,
            attitude_torque_n_m,
            controller_thrusters,
            config.controller,
            config.step_s,
            controller_vehicle.specific_impulse_s,
            pending_duty_cycles=controller_pending,
        )
        if handoff.owner == AuthorityOwner.TARGET and stack_control_active:
            pending_target_duties = allocation.pending_duty_cycles
        else:
            pending_chaser_duties = allocation.pending_duty_cycles
        force_n = (
            allocation.achieved_force_body_n
            if stack_control_active
            else body_to_lvlh @ allocation.achieved_force_body_n
        )
        actual_acceleration = force_n / (
            stack.total_mass_kg if stack_control_active else config.chaser.mass_kg
        )
        if stack_control_active:
            application_point = (
                stack.chaser_center_reference_m
                if handoff.owner == AuthorityOwner.ORION
                else stack.target_center_reference_m
            )
            stack_actual_torque_n_m = allocation.achieved_torque_body_n_m + np.cross(
                application_point - stack.center_of_mass_reference_m, force_n
            )
        else:
            stack_actual_torque_n_m = allocation.achieved_torque_body_n_m
        used_kg = allocation.propellant_used_kg
        if stack_control_active and handoff.owner == AuthorityOwner.TARGET:
            target_fuel_mass_kg = max(0.0, target_fuel_mass_kg - used_kg)
        else:
            fuel_mass_kg = max(0.0, fuel_mass_kg - used_kg)
        velocity_error = desired_velocity - state[3:]
        docked_stack_active = machine.phase in {
            MissionPhase.SOFT_CAPTURE,
            MissionPhase.DOCKED_STACK_CONTROL,
            MissionPhase.COMPLETE,
        }
        row = {
                "run_id": run_id,
                "time_s": float(time_s),
                "scenario": config.name,
                "random_seed": config.random_seed,
                "fidelity": config.fidelity,
                "mean_motion_rad_s": n_rad_s,
                "phase": machine.phase.value,
                "x_m": state[0],
                "y_m": state[1],
                "z_m": state[2],
                "vx_m_s": state[3],
                "vy_m_s": state[4],
                "vz_m_s": state[5],
                "reference_vx_m_s": desired_velocity[0],
                "reference_vy_m_s": desired_velocity[1],
                "reference_vz_m_s": desired_velocity[2],
                "velocity_error_m_s": float(np.linalg.norm(velocity_error)),
                "commanded_fx_n": requested_force_lvlh_n[0],
                "commanded_fy_n": requested_force_lvlh_n[1],
                "commanded_fz_n": requested_force_lvlh_n[2],
                "actual_fx_n": force_n[0],
                "actual_fy_n": force_n[1],
                "actual_fz_n": force_n[2],
                "thrust_n": float(np.linalg.norm(force_n)),
                "fuel_mass_kg": fuel_mass_kg,
                "target_fuel_mass_kg": target_fuel_mass_kg,
                "chaser_propellant_used_kg": initial_fuel_mass_kg - fuel_mass_kg,
                "target_propellant_used_kg": (initial_target_fuel_mass_kg - target_fuel_mass_kg),
                "propellant_used_kg": (
                    initial_fuel_mass_kg
                    - fuel_mass_kg
                    + initial_target_fuel_mass_kg
                    - target_fuel_mass_kg
                ),
                "range_m": range_m,
                "closing_rate_m_s": safety.closing_rate_m_s,
                "closing_rate_limit_m_s": safety.closing_rate_limit_m_s,
                "time_to_contact_s": safety.time_to_contact_s,
                "corridor_margin_m": safety.corridor_margin_m,
                "keep_out_margin_m": safety.keep_out_margin_m,
                "warning": safety.abort.reason if safety.abort else "",
                "abort_mode": abort_mode,
                "chaser_qw": chaser_attitude[0],
                "chaser_qx": chaser_attitude[1],
                "chaser_qy": chaser_attitude[2],
                "chaser_qz": chaser_attitude[3],
                "chaser_wx_rad_s": chaser_attitude[4],
                "chaser_wy_rad_s": chaser_attitude[5],
                "chaser_wz_rad_s": chaser_attitude[6],
                "target_qw": target_attitude[0],
                "target_qx": target_attitude[1],
                "target_qy": target_attitude[2],
                "target_qz": target_attitude[3],
                "target_wx_rad_s": target_attitude[4],
                "target_wy_rad_s": target_attitude[5],
                "target_wz_rad_s": target_attitude[6],
                "commanded_tx_n_m": attitude_torque_n_m[0],
                "commanded_ty_n_m": attitude_torque_n_m[1],
                "commanded_tz_n_m": attitude_torque_n_m[2],
                "actual_tx_n_m": allocation.achieved_torque_body_n_m[0],
                "actual_ty_n_m": allocation.achieved_torque_body_n_m[1],
                "actual_tz_n_m": allocation.achieved_torque_body_n_m[2],
                "stack_actual_tx_n_m": stack_actual_torque_n_m[0],
                "stack_actual_ty_n_m": stack_actual_torque_n_m[1],
                "stack_actual_tz_n_m": stack_actual_torque_n_m[2],
                "allocation_force_residual_n": float(np.linalg.norm(allocation.force_residual_n)),
                "allocation_torque_residual_n_m": float(
                    np.linalg.norm(allocation.torque_residual_n_m)
                ),
                "allocation_saturated": allocation.saturated,
                "minimum_impulse_active": allocation.minimum_impulse_active,
                "active_thruster_count": allocation.active_thruster_count,
                "thruster_duty_cycles": allocation.duty_cycles.tolist(),
                "docked_stack_active": docked_stack_active,
                "stack_total_mass_kg": stack.total_mass_kg if docked_stack_active else np.nan,
                "stack_com_x_m": (
                    stack.center_of_mass_reference_m[0] if docked_stack_active else np.nan
                ),
                "stack_com_y_m": (
                    stack.center_of_mass_reference_m[1] if docked_stack_active else np.nan
                ),
                "stack_com_z_m": (
                    stack.center_of_mass_reference_m[2] if docked_stack_active else np.nan
                ),
                "stack_inertia_xx_kg_m2": (
                    stack.inertia_about_com_reference_kg_m2[0, 0] if docked_stack_active else np.nan
                ),
                "stack_inertia_yy_kg_m2": (
                    stack.inertia_about_com_reference_kg_m2[1, 1] if docked_stack_active else np.nan
                ),
                "stack_inertia_zz_kg_m2": (
                    stack.inertia_about_com_reference_kg_m2[2, 2] if docked_stack_active else np.nan
                ),
                "stack_inertia_xy_kg_m2": (
                    stack.inertia_about_com_reference_kg_m2[0, 1] if docked_stack_active else np.nan
                ),
                "stack_inertia_xz_kg_m2": (
                    stack.inertia_about_com_reference_kg_m2[0, 2] if docked_stack_active else np.nan
                ),
                "stack_inertia_yz_kg_m2": (
                    stack.inertia_about_com_reference_kg_m2[1, 2] if docked_stack_active else np.nan
                ),
                "capture_latched": capture_result is not None,
                "capture_dissipated_energy_j": (
                    capture_result.dissipated_energy_j if capture_result is not None else np.nan
                ),
                "capture_linear_momentum_residual_kg_m_s": (
                    capture_result.linear_momentum_residual_kg_m_s
                    if capture_result is not None
                    else np.nan
                ),
                "capture_angular_momentum_residual_kg_m2_s": (
                    capture_result.angular_momentum_residual_kg_m2_s
                    if capture_result is not None
                    else np.nan
                ),
                "stack_vx_m_s": (stack_velocity[0] if capture_result is not None else np.nan),
                "stack_vy_m_s": (stack_velocity[1] if capture_result is not None else np.nan),
                "stack_vz_m_s": (stack_velocity[2] if capture_result is not None else np.nan),
                "stack_wx_rad_s": (stack_angular_rate[0] if capture_result is not None else np.nan),
                "stack_wy_rad_s": (stack_angular_rate[1] if capture_result is not None else np.nan),
                "stack_wz_rad_s": (stack_angular_rate[2] if capture_result is not None else np.nan),
                "handoff_state": handoff.state.value,
                "controller_authority": handoff.owner.value,
                "command_source": handoff.owner.value,
                "handoff_target": handoff.desired_owner.value,
                "handoff_reason": handoff_reason,
                "handoff_ready": True,
                "handoff_shadow_command_delta": shadow_candidate_delta,
                "handoff_activation_command_delta": activation_command_delta,
                "orion_shadow_fx_n": orion_shadow.force_reference_n[0],
                "orion_shadow_fy_n": orion_shadow.force_reference_n[1],
                "orion_shadow_fz_n": orion_shadow.force_reference_n[2],
                "orion_shadow_tx_n_m": orion_shadow.torque_reference_n_m[0],
                "orion_shadow_ty_n_m": orion_shadow.torque_reference_n_m[1],
                "orion_shadow_tz_n_m": orion_shadow.torque_reference_n_m[2],
                "target_shadow_fx_n": target_shadow.force_reference_n[0],
                "target_shadow_fy_n": target_shadow.force_reference_n[1],
                "target_shadow_fz_n": target_shadow.force_reference_n[2],
                "target_shadow_tx_n_m": target_shadow.torque_reference_n_m[0],
                "target_shadow_ty_n_m": target_shadow.torque_reference_n_m[1],
                "target_shadow_tz_n_m": target_shadow.torque_reference_n_m[2],
                "observed_orion_authority": observed_orion_authority,
                "observed_target_authority": observed_target_authority,
                "authority_invariant_valid": authority_invariant_valid,
                "stack_controller_vehicle": (
                    handoff.owner.value if stack_control_active else "none"
                ),
                "stack_control_active": stack_control_active,
                "active_owner_failure_injected": active_owner_failure_injected,
                "handoff_packet_schema_version": "2.0" if exchange_validation else "",
                "handoff_packet_sequence": exchange_sequence - 1 if exchange_validation else -1,
                "handoff_packet_age_s": (
                    exchange_validation.data_age_s if exchange_validation else np.nan
                ),
                "handoff_frame_id": exchange_frame_id if exchange_validation else "",
                "handoff_frame_consistent": (
                    exchange_validation.frame_consistent if exchange_validation else False
                ),
                "handoff_covariance_valid": (
                    exchange_validation.covariance_valid if exchange_validation else False
                ),
                "handoff_clock_synchronized": (
                    exchange_validation.clock_synchronized if exchange_validation else False
                ),
                "handoff_phase_consistent": (
                    exchange_validation.phase_consistent if exchange_validation else False
                ),
                "handoff_actuator_healthy": (
                    exchange_validation.actuator_healthy if exchange_validation else False
                ),
                "handoff_exchange_reason": (
                    exchange_validation.reason if exchange_validation else "not_received"
                ),
                "port_separation_m": safety.docking_alignment.separation_m,
                "port_lateral_offset_m": safety.docking_alignment.lateral_offset_m,
                "port_angular_error_deg": safety.docking_alignment.angular_error_deg,
                "port_clocking_error_deg": safety.docking_alignment.clocking_error_deg,
                "capture_eligible": safety.capture_eligible,
            }
        abort_response_complete = (
            machine.phase == MissionPhase.ABORT
            and float(time_s) > machine.entered_at_s
            and (
                (abort_mode == "braking" and safety.closing_rate_m_s <= 0.02)
                or (abort_mode == "retreat" and safety.closing_rate_m_s <= 0.0)
            )
        )
        if abort_response_complete:
            events.append(
                {
                    "run_id": run_id,
                    "time_s": float(time_s),
                    "event_type": "abort_response_complete",
                    "phase": machine.phase.value,
                    "detail": abort_mode,
                }
            )
        yield SimulationFrame(
            state=row,
            events=tuple(deepcopy(event) for event in events[event_start:]),
        )
        if machine.phase == MissionPhase.COMPLETE or abort_response_complete:
            break
        if capture_result is None:
            state = _rk4_step(state, actual_acceleration, n_rad_s, config.step_s)
            chaser_attitude = _attitude_rk4_step(
                chaser_attitude,
                chaser_inertia,
                allocation.achieved_torque_body_n_m,
                config.step_s,
            )
            target_attitude = _attitude_rk4_step(
                target_attitude, target_inertia, np.zeros(3), config.step_s
            )
        elif stack_control_active:
            stack_velocity = stack_velocity + actual_acceleration * config.step_s
            angular_momentum = stack.inertia_about_com_reference_kg_m2 @ stack_angular_rate
            angular_acceleration = np.linalg.solve(
                stack.inertia_about_com_reference_kg_m2,
                stack_actual_torque_n_m - np.cross(stack_angular_rate, angular_momentum),
            )
            stack_angular_rate = stack_angular_rate + angular_acceleration * config.step_s



class SimulationSession:
    """Persistent, step-driven facade over the deterministic controlled mission."""

    def __init__(self, config: SimulationConfig) -> None:
        if config.fidelity != "cw":
            raise ValueError("controlled simulation currently requires fidelity='cw'")
        self.config = config
        self.run_id = deterministic_run_id(config)
        self.reset()

    def reset(self) -> None:
        """Reset the session to its reproducible pre-step state."""
        self._frames = _controlled_frames(self.config)
        self._rows: list[dict[str, object]] = []
        self._events: list[dict[str, object]] = []
        self._paused = True
        self._complete = False

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def complete(self) -> bool:
        return self._complete

    @property
    def step_index(self) -> int:
        return len(self._rows) - 1

    @property
    def current(self) -> SimulationFrame | None:
        if not self._rows:
            return None
        return SimulationFrame(deepcopy(self._rows[-1]), ())

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        if not self._complete:
            self._paused = False

    def step(self) -> SimulationFrame:
        """Advance exactly one integration frame, including while paused."""
        if self._complete:
            raise StopIteration("simulation session is complete")
        try:
            frame = next(self._frames)
        except StopIteration:
            self._complete = True
            raise
        self._rows.append(deepcopy(frame.state))
        self._events.extend(deepcopy(event) for event in frame.events)
        terminal = str(frame.state["phase"]) == MissionPhase.COMPLETE.value
        aborted = any(event["event_type"] == "abort_response_complete" for event in frame.events)
        self._complete = terminal or aborted
        if self._complete:
            self._paused = True
        return SimulationFrame(deepcopy(frame.state), tuple(deepcopy(frame.events)))

    def advance(self, steps: int = 1) -> list[SimulationFrame]:
        """Advance a running session by at most ``steps`` frames."""
        if steps < 1:
            raise ValueError("steps must be positive")
        if self._paused:
            return []
        frames: list[SimulationFrame] = []
        for _ in range(steps):
            if self._complete:
                break
            try:
                frames.append(self.step())
            except StopIteration:
                break
        return frames

    def run_to_completion(self) -> SimulationResult:
        self.resume()
        while not self._complete:
            self.advance(steps=1024)
        return self.result()

    def checkpoint(self) -> SimulationCheckpoint:
        if not self._rows:
            raise RuntimeError("step the session before creating a checkpoint")
        state = deepcopy(self._rows[-1])
        return SimulationCheckpoint(
            run_id=self.run_id,
            step_index=self.step_index,
            time_s=float(state["time_s"]),
            state=state,
        )

    def restore(self, checkpoint: SimulationCheckpoint) -> SimulationFrame:
        """Restore by deterministic replay to a validated checkpoint."""
        if checkpoint.run_id != self.run_id:
            raise ValueError("checkpoint belongs to a different simulation configuration")
        self.reset()
        frame: SimulationFrame | None = None
        for _ in range(checkpoint.step_index + 1):
            frame = self.step()
        if frame is None:
            raise ValueError("checkpoint does not contain a simulation frame")
        try:
            pd.testing.assert_series_equal(
                pd.Series(frame.state), pd.Series(checkpoint.state), check_names=False
            )
        except AssertionError as error:
            raise ValueError("checkpoint state does not match deterministic replay") from error
        return frame

    def result(self) -> SimulationResult:
        return SimulationResult(
            telemetry=pd.DataFrame(deepcopy(self._rows)),
            events=pd.DataFrame(deepcopy(self._events)),
        )


def run_controlled(config: SimulationConfig) -> SimulationResult:
    """Run the step-driven controlled mission to completion."""
    return SimulationSession(config).run_to_completion()


def summarize(result: SimulationResult) -> MissionSummary:
    telemetry = result.telemetry
    times = telemetry["time_s"].to_numpy(dtype=np.float64)
    propellant = telemetry["propellant_used_kg"].to_numpy(dtype=np.float64)
    ranges = telemetry["range_m"].to_numpy(dtype=np.float64)
    return MissionSummary(
        terminal_phase=str(telemetry["phase"].iat[-1]),
        elapsed_time_s=float(times[-1]),
        propellant_used_kg=float(propellant[-1]),
        closest_approach_m=float(ranges.min()),
        warning_count=int(telemetry["warning"].ne("").sum()),
    )
