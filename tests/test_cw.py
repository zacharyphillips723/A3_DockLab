import numpy as np

from a3docklab.dynamics.cw import (
    mean_motion,
    propagate,
    state_transition_matrix,
    targeting_initial_velocity,
)


def test_state_transition_is_identity_at_zero_time() -> None:
    n = mean_motion(3.986004418e14, 6_778_137.0)
    np.testing.assert_allclose(state_transition_matrix(n, 0.0), np.eye(6), atol=1e-12)


def test_cross_track_motion_is_harmonic() -> None:
    n = mean_motion(3.986004418e14, 6_778_137.0)
    state0 = np.array([0.0, 0.0, 10.0, 0.0, 0.0, 0.0])
    quarter_period = 0.5 * np.pi / n
    state = propagate(state0, n, quarter_period)
    assert abs(state[2]) < 1e-10
    np.testing.assert_allclose(state[5], -10.0 * n, rtol=1e-10)


def test_targeting_solution_reaches_requested_position() -> None:
    n = mean_motion(3.986004418e14, 6_778_137.0)
    r0 = np.array([20.0, -500.0, 5.0])
    rf = np.zeros(3)
    transfer_time = 600.0
    v0 = targeting_initial_velocity(r0, rf, n, transfer_time)
    final = propagate(np.concatenate((r0, v0)), n, transfer_time)
    np.testing.assert_allclose(final[:3], rf, atol=1e-8)
