import numpy as np

from a3docklab.config import load_config
from a3docklab.dynamics.attitude import rotation_vector_to_quaternion
from a3docklab.dynamics.docking_frames import docking_alignment, docking_port_pose


def _pose(center: np.ndarray, quaternion: np.ndarray, port: object):
    return docking_port_pose(
        center,
        quaternion,
        np.asarray(port.position_body_m),
        np.asarray(port.outward_normal_body),
        np.asarray(port.up_body),
    )


def test_blue_moon_nominal_ports_coincide() -> None:
    config = load_config("configs/scenarios/blue_moon_side.yaml")
    identity = np.array([1.0, 0.0, 0.0, 0.0])
    target = _pose(np.zeros(3), identity, config.docking.target_port)
    chaser = _pose(np.array([0.0, -7.5, 0.0]), identity, config.docking.chaser_port)
    alignment = docking_alignment(chaser, target)
    np.testing.assert_allclose(alignment.position_error_target_port_m, np.zeros(3))
    assert alignment.angular_error_deg == 0.0
    assert alignment.clocking_error_deg == 0.0


def test_alignment_separates_lateral_normal_and_clocking_errors() -> None:
    normal = np.array([0.0, -1.0, 0.0])
    up = np.array([0.0, 0.0, 1.0])
    target = docking_port_pose(np.zeros(3), np.array([1.0, 0.0, 0.0, 0.0]), np.zeros(3), normal, up)
    clocked = rotation_vector_to_quaternion(np.array([0.0, np.deg2rad(5.0), 0.0]))
    chaser = docking_port_pose(
        np.array([0.2, -0.1, 0.3]),
        clocked,
        np.zeros(3),
        -normal,
        up,
    )
    alignment = docking_alignment(chaser, target)
    assert np.isclose(alignment.separation_m, 0.1)
    assert np.isclose(alignment.lateral_offset_m, np.hypot(0.2, 0.3))
    assert np.isclose(alignment.angular_error_deg, 0.0)
    assert np.isclose(abs(alignment.clocking_error_deg), 5.0)


def test_pitch_error_is_reported_as_normal_misalignment() -> None:
    target = docking_port_pose(
        np.zeros(3),
        np.array([1.0, 0.0, 0.0, 0.0]),
        np.zeros(3),
        np.array([0.0, -1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    )
    pitched = rotation_vector_to_quaternion(np.array([np.deg2rad(2.0), 0.0, 0.0]))
    chaser = docking_port_pose(
        np.zeros(3),
        pitched,
        np.zeros(3),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    )
    assert np.isclose(docking_alignment(chaser, target).angular_error_deg, 2.0)
