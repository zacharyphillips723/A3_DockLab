import numpy as np

from a3docklab.dynamics.frames import eci_relative_to_lvlh, lvlh_dcm, lvlh_relative_to_eci


def test_lvlh_frame_matches_documented_axes() -> None:
    dcm = lvlh_dcm(np.array([7_000_000.0, 0.0, 0.0]), np.array([0.0, 7500.0, 0.0]))
    np.testing.assert_allclose(dcm, np.eye(3), atol=1e-15)


def test_relative_state_round_trip() -> None:
    target = np.array([7_000_000.0, 0.0, 0.0, 0.0, 7500.0, 0.0])
    relative = np.array([20.0, -1000.0, 5.0, 0.1, 0.2, -0.02])
    chaser = lvlh_relative_to_eci(target, relative)
    recovered = eci_relative_to_lvlh(target, chaser)
    np.testing.assert_allclose(recovered, relative, atol=1e-9)
