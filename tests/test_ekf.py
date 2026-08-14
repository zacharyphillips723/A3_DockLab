import numpy as np

from a3docklab.estimation.covariance import (
    covariance_is_physical,
    white_acceleration_process_noise,
)
from a3docklab.estimation.ekf import CwExtendedKalmanFilter


def test_prediction_and_joseph_update_preserve_physical_covariance() -> None:
    ekf = CwExtendedKalmanFilter(np.zeros(6), np.eye(6), 0.001)
    ekf.predict(0.1, white_acceleration_process_noise(0.1, 0.01))
    update = ekf.update(np.ones(6) * 0.1, np.eye(6) * 0.01)
    assert covariance_is_physical(update.covariance)
    assert update.normalized_innovation_squared >= 0.0


def test_repeated_measurements_reduce_error_and_uncertainty() -> None:
    truth = np.array([10.0, -20.0, 3.0, 0.1, -0.2, 0.03])
    ekf = CwExtendedKalmanFilter(np.zeros(6), np.eye(6) * 100.0, 0.001)
    initial_error = np.linalg.norm(ekf.state - truth)
    for _ in range(20):
        ekf.predict(0.1, white_acceleration_process_noise(0.1, 0.1))
        update = ekf.update(truth, np.eye(6) * 0.01)
    assert np.linalg.norm(update.state - truth) < initial_error
    assert np.trace(update.covariance) < 1.0


def test_missing_measurement_snapshot_marks_innovation_unavailable() -> None:
    ekf = CwExtendedKalmanFilter(np.zeros(6), np.eye(6), 0.001)
    update = ekf.snapshot_without_measurement()
    assert not update.measurement_used
    assert np.isnan(update.normalized_innovation_squared)
