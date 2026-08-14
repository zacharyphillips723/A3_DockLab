"""Deterministic multi-rate telemetry synthesis for Phase 3."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from a3docklab.estimation.covariance import white_acceleration_process_noise
from a3docklab.estimation.ekf import CwExtendedKalmanFilter
from a3docklab.faults.models import FaultWindow
from a3docklab.simulation.engine import SimulationResult
from a3docklab.telemetry.contracts import FaultConfig, TelemetryConfig

NANOSECONDS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True)
class TelemetryStreams:
    truth: pd.DataFrame
    navigation: pd.DataFrame
    navigation_estimates: pd.DataFrame
    actuation: pd.DataFrame
    communications: pd.DataFrame
    events: pd.DataFrame
    fault_labels: pd.DataFrame
    feature_allowlist: tuple[str, ...]


def _sample_times(end_s: float, rate_hz: float) -> np.ndarray:
    count = int(np.floor(end_s * rate_hz)) + 1
    return np.arange(count, dtype=np.float64) / rate_hz


def _interp(frame: pd.DataFrame, column: str, times: np.ndarray) -> np.ndarray:
    return np.asarray(
        np.interp(
            times,
            frame["time_s"].to_numpy(dtype=np.float64),
            frame[column].to_numpy(dtype=np.float64),
        ),
        dtype=np.float64,
    )


def _active(times: np.ndarray, fault: FaultWindow) -> np.ndarray:
    return (times >= fault.start_s) & (times < fault.start_s + fault.duration_s)


def _faults(config: FaultConfig, name: str) -> list[FaultWindow]:
    return [fault for fault in config.faults if fault.name == name]


def _event_id(run_id: str, event_type: str, event_time_ns: int, detail: str) -> str:
    raw = f"{run_id}:{event_type}:{event_time_ns}:{detail}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def generate_streams(
    result: SimulationResult,
    telemetry_config: TelemetryConfig,
    fault_config: FaultConfig,
    *,
    random_seed: int,
    run_id: str | None = None,
) -> TelemetryStreams:
    """Generate synchronized streams without hiding delay or missing packets."""
    source = result.telemetry
    resolved_run_id = run_id or str(source["run_id"].iat[0])
    end_s = float(source["time_s"].to_numpy(dtype=np.float64)[-1])
    rng = np.random.default_rng(random_seed)

    truth_times = _sample_times(end_s, telemetry_config.truth_rate_hz)
    truth = pd.DataFrame(
        {
            "run_id": resolved_run_id,
            "event_time_ns": np.rint(truth_times * NANOSECONDS_PER_SECOND).astype(np.int64),
            "time_s": truth_times,
            "x_m": _interp(source, "x_m", truth_times),
            "y_m": _interp(source, "y_m", truth_times),
            "z_m": _interp(source, "z_m", truth_times),
            "vx_m_s": _interp(source, "vx_m_s", truth_times),
            "vy_m_s": _interp(source, "vy_m_s", truth_times),
            "vz_m_s": _interp(source, "vz_m_s", truth_times),
            "range_m": _interp(source, "range_m", truth_times),
            "closing_rate_m_s": _interp(source, "closing_rate_m_s", truth_times),
            "propellant_used_kg": _interp(source, "propellant_used_kg", truth_times),
            "chaser_propellant_used_kg": _interp(source, "chaser_propellant_used_kg", truth_times),
            "target_propellant_used_kg": _interp(source, "target_propellant_used_kg", truth_times),
            "corridor_margin_m": _interp(source, "corridor_margin_m", truth_times),
            "keep_out_margin_m": _interp(source, "keep_out_margin_m", truth_times),
            "chaser_qw": _interp(source, "chaser_qw", truth_times),
            "chaser_qx": _interp(source, "chaser_qx", truth_times),
            "chaser_qy": _interp(source, "chaser_qy", truth_times),
            "chaser_qz": _interp(source, "chaser_qz", truth_times),
            "chaser_wx_rad_s": _interp(source, "chaser_wx_rad_s", truth_times),
            "chaser_wy_rad_s": _interp(source, "chaser_wy_rad_s", truth_times),
            "chaser_wz_rad_s": _interp(source, "chaser_wz_rad_s", truth_times),
            "port_separation_m": _interp(source, "port_separation_m", truth_times),
            "port_lateral_offset_m": _interp(source, "port_lateral_offset_m", truth_times),
            "port_angular_error_deg": _interp(source, "port_angular_error_deg", truth_times),
            "port_clocking_error_deg": _interp(source, "port_clocking_error_deg", truth_times),
        }
    )
    phase_indexes = np.searchsorted(source["time_s"].to_numpy(), truth_times, side="right") - 1
    truth["phase"] = source["phase"].to_numpy()[np.maximum(phase_indexes, 0)]
    for column in (
        "handoff_state",
        "controller_authority",
        "command_source",
        "handoff_target",
        "handoff_reason",
        "handoff_packet_schema_version",
        "handoff_frame_id",
        "handoff_exchange_reason",
        "stack_controller_vehicle",
    ):
        truth[column] = source[column].to_numpy()[np.maximum(phase_indexes, 0)]
    truth["handoff_ready"] = _interp(source, "handoff_ready", truth_times) >= 0.5
    truth["handoff_shadow_command_delta"] = _interp(
        source, "handoff_shadow_command_delta", truth_times
    )
    for column in (
        "handoff_activation_command_delta",
        "orion_shadow_fx_n",
        "orion_shadow_fy_n",
        "orion_shadow_fz_n",
        "orion_shadow_tx_n_m",
        "orion_shadow_ty_n_m",
        "orion_shadow_tz_n_m",
        "target_shadow_fx_n",
        "target_shadow_fy_n",
        "target_shadow_fz_n",
        "target_shadow_tx_n_m",
        "target_shadow_ty_n_m",
        "target_shadow_tz_n_m",
    ):
        truth[column] = _interp(source, column, truth_times)
    for column in (
        "observed_orion_authority",
        "observed_target_authority",
        "authority_invariant_valid",
        "stack_control_active",
        "active_owner_failure_injected",
    ):
        truth[column] = _interp(source, column, truth_times) >= 0.5
    truth["handoff_packet_sequence"] = np.rint(
        _interp(source, "handoff_packet_sequence", truth_times)
    ).astype(np.int64)
    truth["handoff_packet_age_s"] = _interp(source, "handoff_packet_age_s", truth_times)
    for column in (
        "handoff_frame_consistent",
        "handoff_covariance_valid",
        "handoff_clock_synchronized",
        "handoff_phase_consistent",
        "handoff_actuator_healthy",
    ):
        truth[column] = _interp(source, column, truth_times) >= 0.5
    truth["capture_eligible"] = _interp(source, "capture_eligible", truth_times) >= 0.5
    truth["docked_stack_active"] = _interp(source, "docked_stack_active", truth_times) >= 0.5
    truth["capture_latched"] = _interp(source, "capture_latched", truth_times) >= 0.5
    for column in (
        "stack_total_mass_kg",
        "stack_com_x_m",
        "stack_com_y_m",
        "stack_com_z_m",
        "stack_inertia_xx_kg_m2",
        "stack_inertia_yy_kg_m2",
        "stack_inertia_zz_kg_m2",
        "stack_inertia_xy_kg_m2",
        "stack_inertia_xz_kg_m2",
        "stack_inertia_yz_kg_m2",
        "capture_dissipated_energy_j",
        "capture_linear_momentum_residual_kg_m_s",
        "capture_angular_momentum_residual_kg_m2_s",
        "stack_vx_m_s",
        "stack_vy_m_s",
        "stack_vz_m_s",
        "stack_wx_rad_s",
        "stack_wy_rad_s",
        "stack_wz_rad_s",
    ):
        truth[column] = _interp(source, column, truth_times)
    quaternion_columns = ["chaser_qw", "chaser_qx", "chaser_qy", "chaser_qz"]
    quaternion_norm = np.linalg.norm(truth[quaternion_columns].to_numpy(), axis=1)
    truth[quaternion_columns] = truth[quaternion_columns].div(quaternion_norm, axis=0)

    sensor_times = _sample_times(end_s, telemetry_config.relative_sensor_rate_hz)
    position = np.column_stack(
        [_interp(source, key, sensor_times) for key in ("x_m", "y_m", "z_m")]
    )
    velocity = np.column_stack(
        [_interp(source, key, sensor_times) for key in ("vx_m_s", "vy_m_s", "vz_m_s")]
    )
    position += rng.normal(0.0, telemetry_config.position_noise_sigma_m, position.shape)
    velocity += rng.normal(0.0, telemetry_config.velocity_noise_sigma_m_s, velocity.shape)
    nav_fault_active = np.zeros(len(sensor_times), dtype=bool)
    for fault in _faults(fault_config, "navigation_drift"):
        active = _active(sensor_times, fault)
        position[active, 1] += fault.severity * (sensor_times[active] - fault.start_s)
        nav_fault_active |= active
    navigation = pd.DataFrame(
        {
            "run_id": resolved_run_id,
            "sensor_id": "relative_navigation",
            "sequence_number": np.arange(len(sensor_times), dtype=np.int64),
            "event_time_ns": np.rint(sensor_times * NANOSECONDS_PER_SECOND).astype(np.int64),
            "receive_time_ns": np.rint(
                (sensor_times + telemetry_config.communications_latency_s) * NANOSECONDS_PER_SECOND
            ).astype(np.int64),
            "nav_x_m": position[:, 0],
            "nav_y_m": position[:, 1],
            "nav_z_m": position[:, 2],
            "nav_vx_m_s": velocity[:, 0],
            "nav_vy_m_s": velocity[:, 1],
            "nav_vz_m_s": velocity[:, 2],
            "measured_range_m": np.linalg.norm(position, axis=1)
            + rng.normal(0.0, telemetry_config.range_noise_sigma_m, len(sensor_times)),
            "position_sigma_m": telemetry_config.position_noise_sigma_m,
            "velocity_sigma_m_s": telemetry_config.velocity_noise_sigma_m_s,
            "channel_valid_mask": 0b111111,
            "fault_active": nav_fault_active,
        }
    )
    estimate_times = _sample_times(end_s, telemetry_config.navigation_rate_hz)
    measurement_covariance = np.diag(
        [telemetry_config.position_noise_sigma_m**2] * 3
        + [telemetry_config.velocity_noise_sigma_m_s**2] * 3
    )
    initial_covariance = np.diag(
        [telemetry_config.estimator_initial_position_sigma_m**2] * 3
        + [telemetry_config.estimator_initial_velocity_sigma_m_s**2] * 3
    )
    estimator = CwExtendedKalmanFilter(
        np.concatenate((position[0], velocity[0])),
        initial_covariance,
        float(source["mean_motion_rad_s"].to_numpy(dtype=np.float64)[0]),
    )
    estimate_rows: list[dict[str, object]] = []
    sensor_index = 0
    previous_time = float(estimate_times[0])
    for estimate_time in estimate_times:
        dt_s = float(estimate_time - previous_time)
        if dt_s > 0.0:
            estimator.predict(
                dt_s,
                white_acceleration_process_noise(
                    dt_s, telemetry_config.estimator_process_acceleration_sigma_m_s2
                ),
            )
        if sensor_index < len(sensor_times) and np.isclose(
            estimate_time, sensor_times[sensor_index], atol=1e-10
        ):
            measurement = np.concatenate((position[sensor_index], velocity[sensor_index]))
            update = estimator.update(measurement, measurement_covariance)
            sensor_index += 1
        else:
            update = estimator.snapshot_without_measurement()
        sigma = np.sqrt(np.maximum(np.diag(update.covariance), 0.0))
        estimate_rows.append(
            {
                "run_id": resolved_run_id,
                "event_time_ns": round(float(estimate_time) * NANOSECONDS_PER_SECOND),
                "estimate_x_m": update.state[0],
                "estimate_y_m": update.state[1],
                "estimate_z_m": update.state[2],
                "estimate_vx_m_s": update.state[3],
                "estimate_vy_m_s": update.state[4],
                "estimate_vz_m_s": update.state[5],
                "position_sigma_x_m": sigma[0],
                "position_sigma_y_m": sigma[1],
                "position_sigma_z_m": sigma[2],
                "velocity_sigma_x_m_s": sigma[3],
                "velocity_sigma_y_m_s": sigma[4],
                "velocity_sigma_z_m_s": sigma[5],
                "covariance_trace": float(np.trace(update.covariance)),
                "innovation_x_m": update.innovation[0],
                "innovation_y_m": update.innovation[1],
                "innovation_z_m": update.innovation[2],
                "innovation_vx_m_s": update.innovation[3],
                "innovation_vy_m_s": update.innovation[4],
                "innovation_vz_m_s": update.innovation[5],
                "normalized_innovation_squared": update.normalized_innovation_squared,
                "nis_threshold": telemetry_config.estimator_nis_threshold,
                "innovation_consistent": (
                    update.normalized_innovation_squared <= telemetry_config.estimator_nis_threshold
                    if update.measurement_used
                    else False
                ),
                "measurement_used": update.measurement_used,
            }
        )
        previous_time = float(estimate_time)
    navigation_estimates = pd.DataFrame(estimate_rows)

    control_times = _sample_times(end_s, telemetry_config.control_rate_hz)
    commanded = np.column_stack(
        [
            _interp(source, key, control_times)
            for key in ("commanded_fx_n", "commanded_fy_n", "commanded_fz_n")
        ]
    )
    actual = np.column_stack(
        [
            _interp(source, key, control_times)
            for key in ("actual_fx_n", "actual_fy_n", "actual_fz_n")
        ]
    )
    commanded_torque = np.column_stack(
        [
            _interp(source, key, control_times)
            for key in ("commanded_tx_n_m", "commanded_ty_n_m", "commanded_tz_n_m")
        ]
    )
    actual_torque = np.column_stack(
        [
            _interp(source, key, control_times)
            for key in ("actual_tx_n_m", "actual_ty_n_m", "actual_tz_n_m")
        ]
    )
    thrust_fault_active = np.zeros(len(control_times), dtype=bool)
    for fault in _faults(fault_config, "thruster_underperformance"):
        active = _active(control_times, fault)
        actual[active] *= fault.severity
        thrust_fault_active |= active
    actuation = pd.DataFrame(
        {
            "run_id": resolved_run_id,
            "event_time_ns": np.rint(control_times * NANOSECONDS_PER_SECOND).astype(np.int64),
            "commanded_fx_n": commanded[:, 0],
            "commanded_fy_n": commanded[:, 1],
            "commanded_fz_n": commanded[:, 2],
            "actual_fx_n": actual[:, 0],
            "actual_fy_n": actual[:, 1],
            "actual_fz_n": actual[:, 2],
            "commanded_tx_n_m": commanded_torque[:, 0],
            "commanded_ty_n_m": commanded_torque[:, 1],
            "commanded_tz_n_m": commanded_torque[:, 2],
            "actual_tx_n_m": actual_torque[:, 0],
            "actual_ty_n_m": actual_torque[:, 1],
            "actual_tz_n_m": actual_torque[:, 2],
            "stack_actual_tx_n_m": _interp(source, "stack_actual_tx_n_m", control_times),
            "stack_actual_ty_n_m": _interp(source, "stack_actual_ty_n_m", control_times),
            "stack_actual_tz_n_m": _interp(source, "stack_actual_tz_n_m", control_times),
            "allocation_force_residual_n": _interp(
                source, "allocation_force_residual_n", control_times
            ),
            "allocation_torque_residual_n_m": _interp(
                source, "allocation_torque_residual_n_m", control_times
            ),
            "saturation_active": _interp(source, "allocation_saturated", control_times) >= 0.5,
            "minimum_impulse_active": _interp(source, "minimum_impulse_active", control_times)
            >= 0.5,
            "active_thruster_count": np.rint(
                _interp(source, "active_thruster_count", control_times)
            ).astype(np.int64),
            "command_response_residual_n": np.linalg.norm(commanded - actual, axis=1),
            "fault_active": thrust_fault_active,
        }
    )
    control_indexes = (
        np.searchsorted(source["time_s"].to_numpy(dtype=np.float64), control_times, side="right")
        - 1
    )
    actuation["thruster_duty_cycles"] = source["thruster_duty_cycles"].to_numpy()[
        np.maximum(control_indexes, 0)
    ]

    comm_times = _sample_times(end_s, telemetry_config.communications_rate_hz)
    latency = np.full(len(comm_times), telemetry_config.communications_latency_s)
    loss_probability = np.full(len(comm_times), telemetry_config.packet_loss_probability)
    comm_fault_active = np.zeros(len(comm_times), dtype=bool)
    for fault in _faults(fault_config, "communications_latency"):
        active = _active(comm_times, fault)
        latency[active] += fault.severity
        loss_probability[active] = np.maximum(
            loss_probability[active], min(0.5, fault.severity / 10.0)
        )
        comm_fault_active |= active
    received = rng.random(len(comm_times)) >= loss_probability
    event_time_ns = np.rint(comm_times * NANOSECONDS_PER_SECOND).astype(np.int64)
    receive_time_ns = np.rint((comm_times + latency) * NANOSECONDS_PER_SECOND).astype(np.int64)
    communications = pd.DataFrame(
        {
            "run_id": resolved_run_id,
            "link_id": "relative_navigation_link",
            "sequence_number": np.arange(len(comm_times), dtype=np.int64),
            "event_time_ns": event_time_ns,
            "receive_time_ns": receive_time_ns,
            "data_age_ns": receive_time_ns - event_time_ns,
            "packet_received": received,
            "link_state": np.where(received, "available", "packet_lost"),
            "fault_active": comm_fault_active,
        }
    )

    event_rows: list[dict[str, object]] = []
    if result.events is not None:
        for row in result.events.to_dict("records"):
            time_ns = round(float(row["time_s"]) * NANOSECONDS_PER_SECOND)
            detail = str(row["detail"])
            event_type = str(row["event_type"])
            event_rows.append(
                {
                    "run_id": resolved_run_id,
                    "event_id": _event_id(resolved_run_id, event_type, time_ns, detail),
                    "event_time_ns": time_ns,
                    "event_type": event_type,
                    "phase": str(row["phase"]),
                    "severity": "warning" if event_type == "abort" else "info",
                    "detail": detail,
                }
            )
    label_rows: list[dict[str, object]] = []
    for index, fault in enumerate(fault_config.faults):
        fault_id = f"fault-{index + 1:03d}"
        onset_ns = round(fault.start_s * NANOSECONDS_PER_SECOND)
        recovery_ns = round((fault.start_s + fault.duration_s) * NANOSECONDS_PER_SECOND)
        label_rows.append(
            {
                "run_id": resolved_run_id,
                "fault_id": fault_id,
                "fault_type": fault.name,
                "onset_time_ns": onset_ns,
                "recovery_time_ns": recovery_ns,
                "severity": fault.severity,
                "affected_channels": list(fault.affected_channels),
            }
        )
        for event_type, event_ns in (("fault_onset", onset_ns), ("fault_recovery", recovery_ns)):
            event_rows.append(
                {
                    "run_id": resolved_run_id,
                    "event_id": _event_id(resolved_run_id, event_type, event_ns, fault_id),
                    "event_time_ns": event_ns,
                    "event_type": event_type,
                    "phase": "",
                    "severity": "warning" if event_type == "fault_onset" else "info",
                    "detail": f"{fault_id}:{fault.name}",
                }
            )
    events = pd.DataFrame(event_rows).sort_values("event_time_ns", ignore_index=True)
    labels = pd.DataFrame(label_rows)
    allowlist = (
        "nav_x_m",
        "nav_y_m",
        "nav_z_m",
        "nav_vx_m_s",
        "nav_vy_m_s",
        "nav_vz_m_s",
        "measured_range_m",
        "position_sigma_m",
        "velocity_sigma_m_s",
        "channel_valid_mask",
        "command_response_residual_n",
        "data_age_ns",
        "packet_received",
    )
    return TelemetryStreams(
        truth,
        navigation,
        navigation_estimates,
        actuation,
        communications,
        events,
        labels,
        allowlist,
    )
