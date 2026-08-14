import numpy as np

from a3docklab.config import ControllerConfig, VehicleConfig
from a3docklab.control.allocation import allocate_wrench, allocation_matrix, default_thruster_layout


def _vehicle() -> VehicleConfig:
    return VehicleConfig(
        name="allocation test vehicle",
        mass_kg=1000.0,
        inertia_kg_m2=(100.0, 120.0, 140.0),
        length_m=4.0,
        diameter_m=2.0,
        max_translation_thrust_n=100.0,
        specific_impulse_s=300.0,
    )


def test_layout_has_six_axis_authority_and_concrete_geometry() -> None:
    thrusters = default_thruster_layout(_vehicle())
    matrix = allocation_matrix(thrusters)
    assert len(thrusters) == 24
    assert np.linalg.matrix_rank(matrix) == 6
    assert all(thruster.maximum_thrust_n > 0.0 for thruster in thrusters)


def test_allocator_tracks_feasible_force_and_torque() -> None:
    vehicle = _vehicle()
    result = allocate_wrench(
        np.array([20.0, -10.0, 5.0]),
        np.array([2.0, -3.0, 4.0]),
        default_thruster_layout(vehicle),
        ControllerConfig(minimum_impulse_s=0.0),
        1.0,
        vehicle.specific_impulse_s,
    )
    np.testing.assert_allclose(result.achieved_force_body_n, [20.0, -10.0, 5.0], atol=1e-7)
    np.testing.assert_allclose(result.achieved_torque_body_n_m, [2.0, -3.0, 4.0], atol=1e-7)
    assert np.all((result.duty_cycles >= 0.0) & (result.duty_cycles <= 1.0))


def test_failed_thruster_is_never_commanded_and_residual_is_reported() -> None:
    vehicle = _vehicle()
    thrusters = default_thruster_layout(vehicle)
    health = np.ones(len(thrusters))
    health[:4] = 0.0
    result = allocate_wrench(
        np.array([500.0, 0.0, 0.0]),
        np.zeros(3),
        thrusters,
        ControllerConfig(minimum_impulse_s=0.0),
        1.0,
        vehicle.specific_impulse_s,
        health,
    )
    np.testing.assert_allclose(result.duty_cycles[:4], 0.0)
    assert np.linalg.norm(result.force_residual_n) > 0.0


def test_minimum_impulse_accumulates_short_commands_into_a_valid_pulse() -> None:
    vehicle = _vehicle()
    pending = None
    fired = False
    for _ in range(8):
        result = allocate_wrench(
            np.array([1.0, 0.0, 0.0]),
            np.zeros(3),
            default_thruster_layout(vehicle),
            ControllerConfig(minimum_impulse_s=0.05),
            1.0,
            vehicle.specific_impulse_s,
            pending_duty_cycles=pending,
        )
        pending = result.pending_duty_cycles
        fired |= result.propellant_used_kg > 0.0
        assert result.minimum_impulse_active
    assert fired
