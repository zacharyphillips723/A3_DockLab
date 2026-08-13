"""Nonlinear two-body truth-model helpers."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def inertial_derivative(
    _time_s: float,
    state: FloatArray,
    mu_m3_s2: float,
    control_acceleration_m_s2: FloatArray | None = None,
) -> FloatArray:
    """Return [velocity, acceleration] for a point mass in an inertial frame."""
    state = np.asarray(state, dtype=np.float64)
    if state.shape != (6,):
        raise ValueError("state must have shape (6,)")
    r = state[:3]
    v = state[3:]
    radius = np.linalg.norm(r)
    if radius <= 0.0:
        raise ValueError("position norm must be positive")
    control = (
        np.zeros(3, dtype=np.float64)
        if control_acceleration_m_s2 is None
        else np.asarray(control_acceleration_m_s2, dtype=np.float64)
    )
    gravity = -mu_m3_s2 * r / radius**3
    return np.concatenate((v, gravity + control))
