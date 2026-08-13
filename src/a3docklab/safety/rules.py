"""Geometry and closing-rate safety primitives."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def closing_rate(position_m: FloatArray, velocity_m_s: FloatArray) -> float:
    """Return positive closing speed along line of sight."""
    r = np.asarray(position_m, dtype=np.float64)
    v = np.asarray(velocity_m_s, dtype=np.float64)
    rho = np.linalg.norm(r)
    if rho <= 0.0:
        return 0.0
    return float(-np.dot(r / rho, v))


def outside_ellipsoidal_keep_out_zone(
    position_m: FloatArray,
    semi_axes_m: FloatArray,
) -> bool:
    """Return True when a point lies on or outside an ellipsoidal KOZ."""
    r = np.asarray(position_m, dtype=np.float64)
    axes = np.asarray(semi_axes_m, dtype=np.float64)
    if r.shape != (3,) or axes.shape != (3,) or np.any(axes <= 0.0):
        raise ValueError("position and positive semi-axes must have shape (3,)")
    return bool(np.sum((r / axes) ** 2) >= 1.0)
