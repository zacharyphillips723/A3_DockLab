"""Six-state CW extended Kalman filter for relative navigation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from a3docklab.dynamics.cw import state_transition_matrix
from a3docklab.estimation.covariance import covariance_is_physical

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class FilterUpdate:
    state: FloatArray
    covariance: FloatArray
    innovation: FloatArray
    innovation_covariance: FloatArray
    normalized_innovation_squared: float
    measurement_used: bool


@dataclass
class CwExtendedKalmanFilter:
    state: FloatArray
    covariance: FloatArray
    mean_motion_rad_s: float

    def __post_init__(self) -> None:
        self.state = np.asarray(self.state, dtype=np.float64)
        self.covariance = np.asarray(self.covariance, dtype=np.float64)
        if self.state.shape != (6,) or not covariance_is_physical(self.covariance):
            raise ValueError("EKF requires a six-state vector and physical 6x6 covariance")
        if self.mean_motion_rad_s <= 0.0:
            raise ValueError("mean motion must be positive")

    def predict(self, dt_s: float, process_noise: FloatArray) -> None:
        transition = state_transition_matrix(self.mean_motion_rad_s, dt_s)
        noise = np.asarray(process_noise, dtype=np.float64)
        if noise.shape != (6, 6):
            raise ValueError("process noise must have shape (6, 6)")
        self.state = np.asarray(transition @ self.state)
        self.covariance = np.asarray(transition @ self.covariance @ transition.T + noise)
        self.covariance = 0.5 * (self.covariance + self.covariance.T)

    def update(self, measurement: FloatArray, measurement_covariance: FloatArray) -> FilterUpdate:
        observed = np.asarray(measurement, dtype=np.float64)
        measurement_noise = np.asarray(measurement_covariance, dtype=np.float64)
        if observed.shape != (6,) or measurement_noise.shape != (6, 6):
            raise ValueError("measurement and covariance must have shapes (6,) and (6, 6)")
        innovation = observed - self.state
        innovation_covariance = self.covariance + measurement_noise
        gain = np.linalg.solve(innovation_covariance, self.covariance).T
        self.state = np.asarray(self.state + gain @ innovation)
        identity = np.eye(6)
        residual_factor = identity - gain
        self.covariance = np.asarray(
            residual_factor @ self.covariance @ residual_factor.T
            + gain @ measurement_noise @ gain.T
        )
        self.covariance = 0.5 * (self.covariance + self.covariance.T)
        nis = float(innovation @ np.linalg.solve(innovation_covariance, innovation))
        return FilterUpdate(
            self.state.copy(),
            self.covariance.copy(),
            innovation,
            innovation_covariance,
            nis,
            True,
        )

    def snapshot_without_measurement(self) -> FilterUpdate:
        return FilterUpdate(
            self.state.copy(),
            self.covariance.copy(),
            np.full(6, np.nan),
            np.full((6, 6), np.nan),
            float("nan"),
            False,
        )
