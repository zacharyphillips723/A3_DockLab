"""Nonlinear two-body truth-model helpers."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_ivp  # type: ignore[import-untyped]

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


def propagate_two_body(
    state0: FloatArray,
    times_s: FloatArray,
    mu_m3_s2: float,
    *,
    rtol: float = 1e-10,
    atol: float = 1e-12,
) -> FloatArray:
    """Propagate an inertial point-mass state at requested times."""
    initial = np.asarray(state0, dtype=np.float64)
    times = np.asarray(times_s, dtype=np.float64)
    if initial.shape != (6,):
        raise ValueError("state0 must have shape (6,)")
    if times.ndim != 1 or len(times) == 0 or times[0] != 0.0 or np.any(np.diff(times) <= 0.0):
        raise ValueError("times_s must be strictly increasing and begin at zero")
    solution = solve_ivp(
        inertial_derivative,
        (float(times[0]), float(times[-1])),
        initial,
        args=(mu_m3_s2,),
        t_eval=times,
        rtol=rtol,
        atol=atol,
        method="DOP853",
    )
    if not solution.success:
        raise RuntimeError(f"two-body propagation failed: {solution.message}")
    return np.asarray(solution.y.T, dtype=np.float64)
