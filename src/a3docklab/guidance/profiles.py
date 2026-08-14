"""Range-dependent approach and abort velocity profiles."""

from __future__ import annotations

import numpy as np

from a3docklab.config import GuidanceConfig, SafetyConfig
from a3docklab.simulation.phases import MissionPhase


def axis_index(axis: str) -> int:
    return {"x": 0, "y": 1, "z": 2}[axis]


def commanded_velocity(
    position_m: np.ndarray,
    phase: MissionPhase,
    guidance: GuidanceConfig,
    safety: SafetyConfig,
    abort_mode: str = "retreat",
) -> np.ndarray:
    command = np.zeros(3, dtype=np.float64)
    axis = axis_index(guidance.docking_axis)
    toward_target = -float(np.sign(position_m[axis]))
    if phase == MissionPhase.FAR_FIELD_APPROACH:
        speed = guidance.far_closing_rate_m_s
    elif phase == MissionPhase.PROXIMITY_OPERATIONS:
        speed = guidance.proximity_closing_rate_m_s
    elif phase == MissionPhase.FINAL_APPROACH:
        speed = guidance.final_closing_rate_m_s
    elif phase == MissionPhase.ABORT:
        speed = 0.0 if abort_mode == "braking" else -safety.abort_retreat_rate_m_s
    else:
        speed = 0.0
    command[axis] = toward_target * speed
    return command
