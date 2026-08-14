import numpy as np

from a3docklab.control.attitude import attitude_pd_torque
from a3docklab.dynamics.attitude import (
    attitude_error_vector,
    propagate_attitude,
    quaternion_to_dcm,
    rotation_vector_to_quaternion,
)


def test_quaternion_rotation_and_error_conventions() -> None:
    quarter_turn = rotation_vector_to_quaternion(np.array([0.0, 0.0, np.pi / 2.0]))
    rotated = quaternion_to_dcm(quarter_turn) @ np.array([1.0, 0.0, 0.0])
    np.testing.assert_allclose(rotated, [0.0, 1.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(
        attitude_error_vector(np.array([1.0, 0.0, 0.0, 0.0]), quarter_turn),
        [0.0, 0.0, np.pi / 2.0],
        atol=1e-12,
    )


def test_spherical_body_conserves_rate_and_matches_constant_rate_rotation() -> None:
    times = np.linspace(0.0, 20.0, 101)
    omega = np.array([0.0, 0.0, 0.02])
    states = propagate_attitude(
        np.array([1.0, 0.0, 0.0, 0.0, *omega]),
        np.array([100.0, 100.0, 100.0]),
        times,
    )
    np.testing.assert_allclose(states[:, 4:], np.tile(omega, (len(times), 1)), atol=1e-12)
    expected = rotation_vector_to_quaternion(omega * times[-1])
    np.testing.assert_allclose(states[-1, :4], expected, atol=1e-10)
    np.testing.assert_allclose(np.linalg.norm(states[:, :4], axis=1), 1.0, atol=1e-12)


def test_torque_free_asymmetric_body_conserves_energy_and_momentum() -> None:
    inertia = np.array([90_000.0, 85_000.0, 70_000.0])
    states = propagate_attitude(
        np.array([1.0, 0.0, 0.0, 0.0, 0.01, -0.015, 0.02]),
        inertia,
        np.linspace(0.0, 300.0, 301),
    )
    energy = 0.5 * np.sum(inertia * states[:, 4:] ** 2, axis=1)
    momentum_norm = np.linalg.norm(inertia * states[:, 4:], axis=1)
    np.testing.assert_allclose(energy, energy[0], rtol=2e-10)
    np.testing.assert_allclose(momentum_norm, momentum_norm[0], rtol=2e-10)


def test_pd_controller_uses_shortest_error_and_saturates() -> None:
    desired = rotation_vector_to_quaternion(np.array([0.0, 0.0, np.deg2rad(10.0)]))
    torque = attitude_pd_torque(
        np.array([1.0, 0.0, 0.0, 0.0]),
        -desired,
        np.zeros(3),
        np.zeros(3),
        np.full(3, 100.0),
        np.full(3, 20.0),
        np.full(3, 5.0),
    )
    np.testing.assert_allclose(torque, [0.0, 0.0, 5.0], atol=1e-12)
