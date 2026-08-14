"""Approach-corridor geometry."""

from __future__ import annotations

import math

import numpy as np


def corridor_margin_m(
    position_m: np.ndarray,
    axis_index: int,
    half_angle_deg: float,
    minimum_radius_m: float,
) -> float:
    """Return positive margin inside a conical docking corridor."""
    axial_range = abs(float(position_m[axis_index]))
    lateral = np.delete(np.asarray(position_m, dtype=np.float64), axis_index)
    allowed = minimum_radius_m + axial_range * math.tan(math.radians(half_angle_deg))
    return float(allowed - np.linalg.norm(lateral))
