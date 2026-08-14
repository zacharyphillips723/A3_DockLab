import numpy as np

from a3docklab.config import ControllerConfig, VehicleConfig
from a3docklab.control.translation import allocate_force, propellant_used_kg


def vehicle() -> VehicleConfig:
    return VehicleConfig(
        name="test",
        mass_kg=1000.0,
        inertia_kg_m2=(1.0, 1.0, 1.0),
        length_m=1.0,
        diameter_m=1.0,
        max_translation_thrust_n=100.0,
        specific_impulse_s=300.0,
    )


def test_allocator_saturates_without_changing_direction() -> None:
    force = allocate_force(np.array([1.0, 0.0, 0.0]), vehicle(), ControllerConfig(), 1.0)
    np.testing.assert_allclose(force, [100.0, 0.0, 0.0])


def test_minimum_impulse_never_exceeds_thruster_authority() -> None:
    force = allocate_force(np.array([1e-9, 0.0, 0.0]), vehicle(), ControllerConfig(), 0.01)
    assert np.linalg.norm(force) <= vehicle().max_translation_thrust_n


def test_zero_force_uses_no_propellant() -> None:
    assert propellant_used_kg(np.zeros(3), 10.0, 300.0) == 0.0
