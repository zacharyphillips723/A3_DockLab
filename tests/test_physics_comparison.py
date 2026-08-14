from pathlib import Path

import numpy as np

from a3docklab.config import load_config
from a3docklab.dynamics.two_body import propagate_two_body
from a3docklab.simulation.physics_comparison import compare_cw_and_two_body, validity_envelope


def test_two_body_circular_orbit_conserves_radius_and_energy() -> None:
    mu = 3.986004418e14
    radius = 6_778_137.0
    speed = np.sqrt(mu / radius)
    initial = np.array([radius, 0.0, 0.0, 0.0, speed, 0.0])
    times = np.linspace(0.0, 3600.0, 121)
    states = propagate_two_body(initial, times, mu)
    radii = np.linalg.norm(states[:, :3], axis=1)
    energies = 0.5 * np.sum(states[:, 3:] ** 2, axis=1) - mu / radii
    np.testing.assert_allclose(radii, radius, rtol=0.0, atol=0.02)
    np.testing.assert_allclose(energies, energies[0], rtol=3e-10)


def test_analytic_and_numerical_cw_agree() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    _, summary = compare_cw_and_two_body(config, duration_s=600.0, step_s=10.0)
    assert summary.maximum_cw_numerical_error_m < 1e-6
    assert summary.maximum_two_body_position_error_m > 0.0


def test_tighter_two_body_tolerances_converge() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    loose, _ = compare_cw_and_two_body(config, duration_s=600.0, step_s=20.0, rtol=1e-8, atol=1e-10)
    tight, _ = compare_cw_and_two_body(
        config, duration_s=600.0, step_s=20.0, rtol=1e-11, atol=1e-13
    )
    difference = np.linalg.norm(
        loose[["two_body_x_m", "two_body_y_m", "two_body_z_m"]].to_numpy()
        - tight[["two_body_x_m", "two_body_y_m", "two_body_z_m"]].to_numpy(),
        axis=1,
    )
    assert difference.max() < 0.2

    tighter, _ = compare_cw_and_two_body(
        config, duration_s=600.0, step_s=20.0, rtol=3e-12, atol=3e-14
    )
    tight_difference = np.linalg.norm(
        tight[["two_body_x_m", "two_body_y_m", "two_body_z_m"]].to_numpy()
        - tighter[["two_body_x_m", "two_body_y_m", "two_body_z_m"]].to_numpy(),
        axis=1,
    )
    assert tight_difference.max() < difference.max()


def test_validity_error_grows_with_large_separation() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    envelope = validity_envelope(config, (100.0, 10_000.0), (1800.0,), step_s=30.0)
    assert envelope["maximum_position_error_m"].iat[1] > envelope["maximum_position_error_m"].iat[0]
