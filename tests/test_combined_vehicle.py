import numpy as np

from a3docklab.config import load_config
from a3docklab.dynamics.combined_vehicle import (
    RigidBodyProperties,
    applied_wrench_response,
    combine_rigid_bodies,
    docked_stack_properties,
)


def test_symmetric_equal_bodies_match_closed_form_parallel_axis_result() -> None:
    inertia = np.diag([1.0, 2.0, 3.0])
    mass, center, combined = combine_rigid_bodies(
        (
            RigidBodyProperties(2.0, np.array([-1.0, 0.0, 0.0]), inertia),
            RigidBodyProperties(2.0, np.array([1.0, 0.0, 0.0]), inertia),
        )
    )
    assert mass == 4.0
    np.testing.assert_allclose(center, np.zeros(3))
    np.testing.assert_allclose(combined, np.diag([2.0, 8.0, 10.0]))


def test_scenario_stack_mass_com_and_inertia_are_physical() -> None:
    config = load_config("configs/scenarios/blue_moon_side.yaml")
    stack = docked_stack_properties(config)
    assert stack.total_mass_kg == config.chaser.mass_kg + config.target.mass_kg
    assert np.all(stack.principal_moments_kg_m2 > 0.0)
    np.testing.assert_allclose(
        stack.inertia_about_com_reference_kg_m2, stack.inertia_about_com_reference_kg_m2.T
    )
    interface_from_chaser = stack.chaser_center_reference_m + np.asarray(
        config.docking.chaser_port.position_body_m
    )
    np.testing.assert_allclose(interface_from_chaser, stack.docking_interface_reference_m)


def test_side_docked_off_center_force_induces_rotation_but_com_force_does_not() -> None:
    stack = docked_stack_properties(load_config("configs/scenarios/blue_moon_side.yaml"))
    force = np.array([100.0, 0.0, 0.0])
    off_center = applied_wrench_response(stack, force, np.zeros(3), stack.chaser_center_reference_m)
    centered = applied_wrench_response(stack, force, np.zeros(3), stack.center_of_mass_reference_m)
    assert np.linalg.norm(off_center.induced_torque_reference_n_m) > 0.0
    assert np.linalg.norm(off_center.angular_acceleration_reference_rad_s2) > 0.0
    np.testing.assert_allclose(centered.angular_acceleration_reference_rad_s2, np.zeros(3))
