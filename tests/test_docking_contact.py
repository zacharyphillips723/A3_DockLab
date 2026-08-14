import numpy as np

from a3docklab.config import load_config
from a3docklab.dynamics.combined_vehicle import RigidBodyProperties, docked_stack_properties
from a3docklab.dynamics.docking_contact import (
    ContactParameters,
    compliant_contact_force,
    latch_capture,
)


def test_compliant_contact_is_zero_without_penetration() -> None:
    contact = compliant_contact_force(
        0.0, 1.0, np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
    )
    np.testing.assert_allclose(contact.force_on_chaser_reference_n, np.zeros(3))


def test_contact_force_opposes_tangential_slip_and_is_friction_limited() -> None:
    parameters = ContactParameters(friction_coefficient=0.3)
    contact = compliant_contact_force(
        0.01,
        0.02,
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        parameters,
    )
    assert contact.normal_force_n > 0.0
    assert contact.force_on_chaser_reference_n[0] < 0.0
    assert contact.tangential_force_n <= parameters.friction_coefficient * contact.normal_force_n


def test_latch_preserves_linear_and_angular_momentum_and_dissipates_energy() -> None:
    config = load_config("configs/scenarios/blue_moon_side.yaml")
    stack = docked_stack_properties(config)
    chaser = RigidBodyProperties(
        config.chaser.mass_kg,
        stack.chaser_center_reference_m,
        np.diag(config.chaser.inertia_kg_m2),
    )
    target = RigidBodyProperties(
        config.target.mass_kg,
        stack.target_center_reference_m,
        np.diag(config.target.inertia_kg_m2),
    )
    result = latch_capture(
        chaser,
        target,
        np.array([0.01, 0.04, -0.005]),
        np.zeros(3),
        np.array([0.001, 0.0, -0.002]),
        np.zeros(3),
        stack,
    )
    assert result.linear_momentum_residual_kg_m_s < 1e-10
    assert result.angular_momentum_residual_kg_m2_s < 1e-10
    assert result.dissipated_energy_j >= 0.0
