"""ECI and target-centered LVLH state transformations."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def lvlh_dcm(target_position_eci_m: FloatArray, target_velocity_eci_m_s: FloatArray) -> FloatArray:
    """Return the ECI-to-LVLH direction cosine matrix.

    Rows are +x radial outward, +y along-track, and +z orbit normal.
    """
    position = np.asarray(target_position_eci_m, dtype=np.float64)
    velocity = np.asarray(target_velocity_eci_m_s, dtype=np.float64)
    if position.shape != (3,) or velocity.shape != (3,):
        raise ValueError("target position and velocity must have shape (3,)")
    radial = position / np.linalg.norm(position)
    normal_raw = np.cross(position, velocity)
    normal_norm = np.linalg.norm(normal_raw)
    if normal_norm <= 0.0:
        raise ValueError("target position and velocity must define an orbital plane")
    normal = normal_raw / normal_norm
    along_track = np.cross(normal, radial)
    return np.asarray(np.vstack((radial, along_track, normal)), dtype=np.float64)


def orbital_rate_rad_s(
    target_position_eci_m: FloatArray, target_velocity_eci_m_s: FloatArray
) -> float:
    position = np.asarray(target_position_eci_m, dtype=np.float64)
    velocity = np.asarray(target_velocity_eci_m_s, dtype=np.float64)
    return float(np.linalg.norm(np.cross(position, velocity)) / np.dot(position, position))


def eci_relative_to_lvlh(target_state_eci: FloatArray, chaser_state_eci: FloatArray) -> FloatArray:
    target = np.asarray(target_state_eci, dtype=np.float64)
    chaser = np.asarray(chaser_state_eci, dtype=np.float64)
    if target.shape != (6,) or chaser.shape != (6,):
        raise ValueError("target and chaser states must have shape (6,)")
    dcm = lvlh_dcm(target[:3], target[3:])
    position = dcm @ (chaser[:3] - target[:3])
    omega = np.array([0.0, 0.0, orbital_rate_rad_s(target[:3], target[3:])])
    velocity = dcm @ (chaser[3:] - target[3:]) - np.cross(omega, position)
    return np.asarray(np.concatenate((position, velocity)), dtype=np.float64)


def lvlh_relative_to_eci(
    target_state_eci: FloatArray, relative_state_lvlh: FloatArray
) -> FloatArray:
    target = np.asarray(target_state_eci, dtype=np.float64)
    relative = np.asarray(relative_state_lvlh, dtype=np.float64)
    if target.shape != (6,) or relative.shape != (6,):
        raise ValueError("target and relative states must have shape (6,)")
    dcm = lvlh_dcm(target[:3], target[3:])
    omega = np.array([0.0, 0.0, orbital_rate_rad_s(target[:3], target[3:])])
    position = target[:3] + dcm.T @ relative[:3]
    velocity = target[3:] + dcm.T @ (relative[3:] + np.cross(omega, relative[:3]))
    return np.asarray(np.concatenate((position, velocity)), dtype=np.float64)
