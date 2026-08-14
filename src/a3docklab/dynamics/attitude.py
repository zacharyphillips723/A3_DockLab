"""Quaternion kinematics and torque-driven rigid-body attitude dynamics.

Quaternions use scalar-first ``wxyz`` order and rotate body-frame vectors into
the reference frame. Angular velocity and applied torque are body-frame values.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from scipy.integrate import solve_ivp  # type: ignore[import-untyped]

FloatArray = NDArray[np.float64]


def normalize_quaternion(quaternion_wxyz: FloatArray) -> FloatArray:
    quaternion = np.asarray(quaternion_wxyz, dtype=np.float64)
    if quaternion.shape != (4,):
        raise ValueError("quaternion must have shape (4,)")
    norm = float(np.linalg.norm(quaternion))
    if norm <= 0.0:
        raise ValueError("quaternion norm must be positive")
    return np.asarray(quaternion / norm, dtype=np.float64)


def quaternion_multiply(left_wxyz: FloatArray, right_wxyz: FloatArray) -> FloatArray:
    """Return the Hamilton product of two scalar-first quaternions."""
    w1, x1, y1, z1 = np.asarray(left_wxyz, dtype=np.float64)
    w2, x2, y2, z2 = np.asarray(right_wxyz, dtype=np.float64)
    return np.array(
        [
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        ],
        dtype=np.float64,
    )


def quaternion_conjugate(quaternion_wxyz: FloatArray) -> FloatArray:
    quaternion = normalize_quaternion(quaternion_wxyz)
    return np.array([quaternion[0], -quaternion[1], -quaternion[2], -quaternion[3]])


def quaternion_to_dcm(quaternion_wxyz: FloatArray) -> FloatArray:
    """Return the body-to-reference direction cosine matrix."""
    w, x, y, z = normalize_quaternion(quaternion_wxyz)
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - w * z), 2.0 * (x * z + w * y)],
            [2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - w * x)],
            [2.0 * (x * z - w * y), 2.0 * (y * z + w * x), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def rotation_vector_to_quaternion(rotation_vector_rad: FloatArray) -> FloatArray:
    vector = np.asarray(rotation_vector_rad, dtype=np.float64)
    if vector.shape != (3,):
        raise ValueError("rotation vector must have shape (3,)")
    angle = float(np.linalg.norm(vector))
    if angle < 1e-14:
        return normalize_quaternion(np.array([1.0, *(0.5 * vector)]))
    axis = vector / angle
    return np.asarray(np.concatenate(([np.cos(angle / 2.0)], axis * np.sin(angle / 2.0))))


def attitude_error_vector(
    current_body_to_reference_wxyz: FloatArray,
    desired_body_to_reference_wxyz: FloatArray,
) -> FloatArray:
    """Return the shortest body-frame rotation vector from current to desired."""
    current = normalize_quaternion(current_body_to_reference_wxyz)
    desired = normalize_quaternion(desired_body_to_reference_wxyz)
    error = quaternion_multiply(quaternion_conjugate(current), desired)
    if error[0] < 0.0:
        error = -error
    vector_norm = float(np.linalg.norm(error[1:]))
    if vector_norm < 1e-14:
        return np.asarray(2.0 * error[1:], dtype=np.float64)
    angle = 2.0 * np.arctan2(vector_norm, float(error[0]))
    return np.asarray(error[1:] * angle / vector_norm, dtype=np.float64)


def rigid_body_derivative(
    _time_s: float,
    state: FloatArray,
    inertia_kg_m2: FloatArray,
    torque_body_n_m: FloatArray,
) -> FloatArray:
    """Return derivatives for ``[q_wxyz, omega_body]``."""
    values = np.asarray(state, dtype=np.float64)
    inertia = np.asarray(inertia_kg_m2, dtype=np.float64)
    torque = np.asarray(torque_body_n_m, dtype=np.float64)
    if values.shape != (7,) or inertia.shape != (3,) or torque.shape != (3,):
        raise ValueError("state, inertia, and torque must have shapes (7,), (3,), and (3,)")
    quaternion = normalize_quaternion(values[:4])
    omega = values[4:]
    quaternion_dot = 0.5 * quaternion_multiply(quaternion, np.array([0.0, *omega]))
    angular_momentum = inertia * omega
    omega_dot = (torque - np.cross(omega, angular_momentum)) / inertia
    return np.asarray(np.concatenate((quaternion_dot, omega_dot)), dtype=np.float64)


def propagate_attitude(
    initial_state: FloatArray,
    inertia_kg_m2: FloatArray,
    times_s: FloatArray,
    torque_body_n_m: FloatArray | None = None,
    *,
    relative_tolerance: float = 1e-10,
    absolute_tolerance: float = 1e-12,
) -> FloatArray:
    """Propagate a rigid body at requested times under constant body torque."""
    times = np.asarray(times_s, dtype=np.float64)
    torque = np.zeros(3) if torque_body_n_m is None else np.asarray(torque_body_n_m)
    if times.ndim != 1 or len(times) == 0 or np.any(np.diff(times) < 0.0):
        raise ValueError("times_s must be a nonempty, nondecreasing vector")
    if len(times) == 1:
        state = np.asarray(initial_state, dtype=np.float64).copy()
        if state.shape != (7,):
            raise ValueError("initial_state must have shape (7,)")
        state[:4] = normalize_quaternion(state[:4])
        return state[None, :]
    result = solve_ivp(
        rigid_body_derivative,
        (float(times[0]), float(times[-1])),
        np.asarray(initial_state, dtype=np.float64),
        args=(np.asarray(inertia_kg_m2, dtype=np.float64), torque),
        t_eval=times,
        method="DOP853",
        rtol=relative_tolerance,
        atol=absolute_tolerance,
    )
    if not result.success:
        raise RuntimeError(f"attitude propagation failed: {result.message}")
    states = np.asarray(result.y.T, dtype=np.float64)
    states[:, :4] /= np.linalg.norm(states[:, :4], axis=1)[:, None]
    return states
