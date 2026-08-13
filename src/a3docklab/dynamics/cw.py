"""Clohessy-Wiltshire relative-motion dynamics.

Frame convention:
    x: radial outward from Earth
    y: along-track in the target's direction of motion
    z: cross-track completing a right-handed frame
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def mean_motion(mu_m3_s2: float, semi_major_axis_m: float) -> float:
    """Return circular-orbit mean motion in rad/s."""
    if mu_m3_s2 <= 0.0 or semi_major_axis_m <= 0.0:
        raise ValueError("mu and semi-major axis must be positive")
    return float(np.sqrt(mu_m3_s2 / semi_major_axis_m**3))


def state_transition_matrix(n_rad_s: float, dt_s: float) -> FloatArray:
    """Return the 6x6 CW state-transition matrix for elapsed time ``dt_s``."""
    if n_rad_s <= 0.0:
        raise ValueError("mean motion must be positive")

    nt = n_rad_s * dt_s
    c = np.cos(nt)
    s = np.sin(nt)
    n = n_rad_s

    phi = np.array(
        [
            [4 - 3 * c, 0, 0, s / n, 2 * (1 - c) / n, 0],
            [6 * (s - nt), 1, 0, -2 * (1 - c) / n, (4 * s - 3 * nt) / n, 0],
            [0, 0, c, 0, 0, s / n],
            [3 * n * s, 0, 0, c, 2 * s, 0],
            [-6 * n * (1 - c), 0, 0, -2 * s, 4 * c - 3, 0],
            [0, 0, -n * s, 0, 0, c],
        ],
        dtype=np.float64,
    )
    return phi


def propagate(state0: FloatArray, n_rad_s: float, dt_s: float) -> FloatArray:
    """Propagate a six-element relative state [x,y,z,vx,vy,vz]."""
    state = np.asarray(state0, dtype=np.float64)
    if state.shape != (6,):
        raise ValueError("state0 must have shape (6,)")
    return state_transition_matrix(n_rad_s, dt_s) @ state


def derivative(
    state: FloatArray, n_rad_s: float, acceleration_m_s2: FloatArray | None = None
) -> FloatArray:
    """Return the continuous-time CW state derivative with optional control acceleration."""
    state = np.asarray(state, dtype=np.float64)
    if state.shape != (6,):
        raise ValueError("state must have shape (6,)")
    acceleration = (
        np.zeros(3, dtype=np.float64)
        if acceleration_m_s2 is None
        else np.asarray(acceleration_m_s2, dtype=np.float64)
    )
    if acceleration.shape != (3,):
        raise ValueError("acceleration_m_s2 must have shape (3,)")

    x, _y, z, vx, vy, vz = state
    ax, ay, az = acceleration
    return np.array(
        [
            vx,
            vy,
            vz,
            3 * n_rad_s**2 * x + 2 * n_rad_s * vy + ax,
            -2 * n_rad_s * vx + ay,
            -(n_rad_s**2) * z + az,
        ],
        dtype=np.float64,
    )


def targeting_initial_velocity(
    r0_m: FloatArray,
    rf_m: FloatArray,
    n_rad_s: float,
    transfer_time_s: float,
) -> FloatArray:
    """Solve the CW two-point boundary problem for initial relative velocity.

    Raises ``numpy.linalg.LinAlgError`` near singular transfer times. Production
    guidance should check the condition number and reject ill-conditioned plans.
    """
    r0 = np.asarray(r0_m, dtype=np.float64)
    rf = np.asarray(rf_m, dtype=np.float64)
    if r0.shape != (3,) or rf.shape != (3,):
        raise ValueError("r0_m and rf_m must have shape (3,)")

    phi = state_transition_matrix(n_rad_s, transfer_time_s)
    phi_rr = phi[:3, :3]
    phi_rv = phi[:3, 3:]
    return np.asarray(np.linalg.solve(phi_rv, rf - phi_rr @ r0), dtype=np.float64)
