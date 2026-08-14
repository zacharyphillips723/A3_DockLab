"""Docking-port rigid transforms and relative alignment metrics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from a3docklab.dynamics.attitude import quaternion_to_dcm

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class DockingAlignment:
    position_error_target_port_m: FloatArray
    separation_m: float
    lateral_offset_m: float
    angular_error_deg: float
    clocking_error_deg: float


def capture_eligible(
    alignment: DockingAlignment,
    *,
    capture_distance_m: float,
    maximum_lateral_offset_m: float,
    maximum_angular_error_deg: float,
    maximum_clocking_error_deg: float,
) -> bool:
    """Return whether port geometry lies inside the configured capture envelope."""
    return (
        alignment.separation_m <= capture_distance_m
        and alignment.lateral_offset_m <= maximum_lateral_offset_m
        and alignment.angular_error_deg <= maximum_angular_error_deg
        and abs(alignment.clocking_error_deg) <= maximum_clocking_error_deg
    )


def _unit(vector: FloatArray, label: str) -> FloatArray:
    value = np.asarray(vector, dtype=np.float64)
    if value.shape != (3,):
        raise ValueError(f"{label} must have shape (3,)")
    norm = float(np.linalg.norm(value))
    if norm <= 0.0:
        raise ValueError(f"{label} must have positive norm")
    return np.asarray(value / norm, dtype=np.float64)


def docking_port_pose(
    center_position_reference_m: FloatArray,
    body_to_reference_wxyz: FloatArray,
    port_position_body_m: FloatArray,
    port_normal_body: FloatArray,
    port_up_body: FloatArray,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Return port position, outward normal, and clocking reference in the reference frame."""
    rotation = quaternion_to_dcm(body_to_reference_wxyz)
    normal_body = _unit(port_normal_body, "port normal")
    up_body = _unit(port_up_body, "port up")
    if abs(float(np.dot(normal_body, up_body))) > 1e-10:
        raise ValueError("port normal and up vectors must be orthogonal")
    position = np.asarray(center_position_reference_m, dtype=np.float64)
    offset = np.asarray(port_position_body_m, dtype=np.float64)
    if position.shape != (3,) or offset.shape != (3,):
        raise ValueError("center and port positions must have shape (3,)")
    return position + rotation @ offset, rotation @ normal_body, rotation @ up_body


def docking_alignment(
    chaser_pose: tuple[FloatArray, FloatArray, FloatArray],
    target_pose: tuple[FloatArray, FloatArray, FloatArray],
) -> DockingAlignment:
    """Measure chaser-port alignment in the target docking-port frame."""
    chaser_position, chaser_normal, chaser_up = chaser_pose
    target_position, target_normal, target_up = target_pose
    target_z = _unit(target_normal, "target normal")
    target_y = _unit(target_up, "target up")
    target_x = _unit(np.cross(target_y, target_z), "target lateral")
    target_dcm = np.vstack((target_x, target_y, target_z))
    position_error = target_dcm @ (chaser_position - target_position)
    normal_dot = float(np.clip(np.dot(_unit(chaser_normal, "chaser normal"), -target_z), -1.0, 1.0))
    angular_error = float(np.degrees(np.arccos(normal_dot)))
    chaser_up_projected = chaser_up - np.dot(chaser_up, target_z) * target_z
    if np.linalg.norm(chaser_up_projected) < 1e-12:
        clocking_error = 180.0
    else:
        projected = _unit(chaser_up_projected, "projected chaser up")
        clocking_error = float(
            np.degrees(
                np.arctan2(
                    np.dot(target_z, np.cross(target_y, projected)), np.dot(target_y, projected)
                )
            )
        )
    return DockingAlignment(
        position_error_target_port_m=np.asarray(position_error, dtype=np.float64),
        separation_m=abs(float(position_error[2])),
        lateral_offset_m=float(np.linalg.norm(position_error[:2])),
        angular_error_deg=angular_error,
        clocking_error_deg=clocking_error,
    )
