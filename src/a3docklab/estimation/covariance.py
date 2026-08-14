"""Covariance helpers for relative-navigation estimation."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def white_acceleration_process_noise(dt_s: float, sigma_acceleration_m_s2: float) -> FloatArray:
    """Return a six-state discrete covariance for independent white acceleration."""
    if dt_s <= 0.0 or sigma_acceleration_m_s2 < 0.0:
        raise ValueError("step must be positive and acceleration sigma nonnegative")
    variance = sigma_acceleration_m_s2**2
    block = variance * np.array(
        [[dt_s**3 / 3.0, dt_s**2 / 2.0], [dt_s**2 / 2.0, dt_s]],
        dtype=np.float64,
    )
    covariance = np.zeros((6, 6), dtype=np.float64)
    for axis in range(3):
        indexes = np.array([axis, axis + 3])
        covariance[np.ix_(indexes, indexes)] = block
    return covariance


def covariance_is_physical(covariance: FloatArray, tolerance: float = 1e-12) -> bool:
    value = np.asarray(covariance, dtype=np.float64)
    return bool(
        value.shape == (6, 6)
        and np.all(np.isfinite(value))
        and np.allclose(value, value.T, atol=tolerance)
        and np.min(np.linalg.eigvalsh(value)) >= -tolerance
    )
