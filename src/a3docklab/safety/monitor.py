"""Independent deterministic rendezvous safety monitor."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from a3docklab.config import SimulationConfig
from a3docklab.dynamics.docking_frames import (
    DockingAlignment,
    capture_eligible,
    docking_alignment,
    docking_port_pose,
)
from a3docklab.guidance.aborts import AbortDecision, AbortMode
from a3docklab.guidance.profiles import axis_index
from a3docklab.safety.geometry import corridor_margin_m
from a3docklab.safety.rules import closing_rate
from a3docklab.simulation.phases import MissionPhase


@dataclass(frozen=True)
class SafetyStatus:
    closing_rate_m_s: float
    closing_rate_limit_m_s: float
    corridor_margin_m: float
    keep_out_margin_m: float
    time_to_contact_s: float
    docking_alignment: DockingAlignment
    capture_eligible: bool
    abort: AbortDecision | None = None


def closing_limit(phase: MissionPhase, config: SimulationConfig) -> float:
    if phase == MissionPhase.FAR_FIELD_APPROACH:
        return config.guidance.far_closing_rate_m_s + config.safety.closing_rate_margin_m_s
    if phase == MissionPhase.PROXIMITY_OPERATIONS:
        return config.guidance.proximity_closing_rate_m_s + config.safety.closing_rate_margin_m_s
    if phase == MissionPhase.FINAL_APPROACH:
        return config.docking.max_closing_rate_m_s + config.safety.closing_rate_margin_m_s
    return max(config.guidance.far_closing_rate_m_s, config.safety.abort_retreat_rate_m_s)


def evaluate_safety(
    state: np.ndarray,
    phase: MissionPhase,
    config: SimulationConfig,
    chaser_quaternion_wxyz: np.ndarray | None = None,
    target_quaternion_wxyz: np.ndarray | None = None,
) -> SafetyStatus:
    position = state[:3]
    velocity = state[3:]
    range_m = float(np.linalg.norm(position))
    rate = closing_rate(position, velocity)
    limit = closing_limit(phase, config)
    corridor = corridor_margin_m(
        position,
        axis_index(config.guidance.docking_axis),
        config.safety.corridor_half_angle_deg,
        config.safety.corridor_min_radius_m,
    )
    keep_out = range_m - config.safety.keep_out_radius_m
    time_to_contact = range_m / rate if rate > 0.0 else float("inf")
    chaser_quaternion = (
        np.asarray(config.attitude.chaser_initial_quaternion_wxyz)
        if chaser_quaternion_wxyz is None
        else np.asarray(chaser_quaternion_wxyz)
    )
    target_quaternion = (
        np.asarray(config.attitude.target_quaternion_wxyz)
        if target_quaternion_wxyz is None
        else np.asarray(target_quaternion_wxyz)
    )
    # The current translational state is port-origin relative position. Body-frame
    # port offsets become active when combined vehicle center-of-mass states land.
    chaser_port = config.docking.chaser_port
    target_port = config.docking.target_port
    chaser_pose = docking_port_pose(
        position,
        chaser_quaternion,
        np.zeros(3),
        np.asarray(chaser_port.outward_normal_body),
        np.asarray(chaser_port.up_body),
    )
    target_pose = docking_port_pose(
        np.zeros(3),
        target_quaternion,
        np.zeros(3),
        np.asarray(target_port.outward_normal_body),
        np.asarray(target_port.up_body),
    )
    alignment = docking_alignment(chaser_pose, target_pose)
    eligible = capture_eligible(
        alignment,
        capture_distance_m=config.docking.capture_distance_m,
        maximum_lateral_offset_m=config.docking.max_lateral_offset_m,
        maximum_angular_error_deg=config.docking.max_angular_error_deg,
        maximum_clocking_error_deg=config.docking.max_clocking_error_deg,
    )
    abort: AbortDecision | None = None
    approach_phases = {
        MissionPhase.FAR_FIELD_APPROACH,
        MissionPhase.PROXIMITY_OPERATIONS,
        MissionPhase.FINAL_APPROACH,
    }
    if phase in approach_phases and rate > limit:
        abort = AbortDecision(AbortMode.BRAKING, "closing_rate_limit")
    elif phase in approach_phases and corridor < 0.0:
        abort = AbortDecision(AbortMode.RETREAT, "approach_corridor_violation")
    elif keep_out < 0.0 and phase not in {
        MissionPhase.FINAL_APPROACH,
        MissionPhase.SOFT_CAPTURE,
        MissionPhase.DOCKED_STACK_CONTROL,
        MissionPhase.COMPLETE,
    }:
        abort = AbortDecision(AbortMode.RETREAT, "unauthorized_keep_out_entry")
    elif (
        phase == MissionPhase.FINAL_APPROACH
        and range_m <= config.safety.terminal_alignment_gate_range_m
    ):
        if alignment.angular_error_deg > config.docking.max_angular_error_deg:
            abort = AbortDecision(AbortMode.RETREAT, "docking_angular_misalignment")
        elif abs(alignment.clocking_error_deg) > config.docking.max_clocking_error_deg:
            abort = AbortDecision(AbortMode.RETREAT, "docking_clocking_misalignment")
        elif alignment.lateral_offset_m > config.docking.max_lateral_offset_m:
            abort = AbortDecision(AbortMode.RETREAT, "docking_lateral_misalignment")
    return SafetyStatus(
        rate, limit, corridor, keep_out, time_to_contact, alignment, eligible, abort
    )
