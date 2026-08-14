"""Quaternion-error proportional-derivative attitude control."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from a3docklab.dynamics.attitude import attitude_error_vector

FloatArray = NDArray[np.float64]


def attitude_pd_torque(
    current_quaternion_wxyz: FloatArray,
    desired_quaternion_wxyz: FloatArray,
    angular_rate_body_rad_s: FloatArray,
    desired_angular_rate_body_rad_s: FloatArray,
    proportional_gain_n_m_per_rad: FloatArray,
    derivative_gain_n_m_s_per_rad: FloatArray,
    maximum_torque_n_m: FloatArray,
) -> FloatArray:
    """Return independently saturated body torque for a desired attitude and rate."""
    attitude_error = attitude_error_vector(current_quaternion_wxyz, desired_quaternion_wxyz)
    rate_error = np.asarray(desired_angular_rate_body_rad_s) - np.asarray(angular_rate_body_rad_s)
    torque = (
        np.asarray(proportional_gain_n_m_per_rad) * attitude_error
        + np.asarray(derivative_gain_n_m_s_per_rad) * rate_error
    )
    limits = np.asarray(maximum_torque_n_m, dtype=np.float64)
    if torque.shape != (3,) or limits.shape != (3,) or np.any(limits <= 0.0):
        raise ValueError(
            "controller vectors must have shape (3,) and torque limits must be positive"
        )
    return np.asarray(np.clip(torque, -limits, limits), dtype=np.float64)
