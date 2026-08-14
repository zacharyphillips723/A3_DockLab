"""Docked-stack mass properties and force/torque coupling."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from a3docklab.config import SimulationConfig
from a3docklab.dynamics.attitude import quaternion_to_dcm

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class RigidBodyProperties:
    mass_kg: float
    center_of_mass_reference_m: FloatArray
    inertia_about_com_reference_kg_m2: FloatArray


@dataclass(frozen=True)
class CombinedVehicleProperties:
    total_mass_kg: float
    center_of_mass_reference_m: FloatArray
    inertia_about_com_reference_kg_m2: FloatArray
    principal_moments_kg_m2: FloatArray
    principal_axes_reference: FloatArray
    chaser_center_reference_m: FloatArray
    target_center_reference_m: FloatArray
    docking_interface_reference_m: FloatArray


@dataclass(frozen=True)
class WrenchResponse:
    linear_acceleration_reference_m_s2: FloatArray
    angular_acceleration_reference_rad_s2: FloatArray
    induced_torque_reference_n_m: FloatArray


def parallel_axis_shift(
    inertia_about_com: FloatArray, mass_kg: float, offset_m: FloatArray
) -> FloatArray:
    """Shift a 3x3 inertia tensor from its COM by ``offset_m``."""
    inertia = np.asarray(inertia_about_com, dtype=np.float64)
    offset = np.asarray(offset_m, dtype=np.float64)
    if inertia.shape != (3, 3) or offset.shape != (3,) or mass_kg <= 0.0:
        raise ValueError("inertia, offset, and mass must describe a valid rigid body")
    return np.asarray(
        inertia + mass_kg * (np.dot(offset, offset) * np.eye(3) - np.outer(offset, offset)),
        dtype=np.float64,
    )


def combine_rigid_bodies(
    bodies: tuple[RigidBodyProperties, ...],
) -> tuple[float, FloatArray, FloatArray]:
    """Return total mass, combined COM, and full inertia tensor in one reference frame."""
    if not bodies:
        raise ValueError("at least one rigid body is required")
    total_mass = sum(body.mass_kg for body in bodies)
    center = (
        sum(
            (body.mass_kg * body.center_of_mass_reference_m for body in bodies),
            start=np.zeros(3, dtype=np.float64),
        )
        / total_mass
    )
    inertia = sum(
        (
            parallel_axis_shift(
                body.inertia_about_com_reference_kg_m2,
                body.mass_kg,
                body.center_of_mass_reference_m - center,
            )
            for body in bodies
        ),
        start=np.zeros((3, 3), dtype=np.float64),
    )
    return float(total_mass), np.asarray(center), np.asarray(inertia)


def docked_stack_properties(config: SimulationConfig) -> CombinedVehicleProperties:
    """Construct aligned docked-stack properties in the target body/LVLH frame."""
    target_rotation = quaternion_to_dcm(np.asarray(config.attitude.target_quaternion_wxyz))
    chaser_rotation = quaternion_to_dcm(np.asarray(config.attitude.chaser_initial_quaternion_wxyz))
    target_center = np.zeros(3, dtype=np.float64)
    target_port = target_rotation @ np.asarray(config.docking.target_port.position_body_m)
    chaser_port_offset = chaser_rotation @ np.asarray(config.docking.chaser_port.position_body_m)
    chaser_center = target_port - chaser_port_offset
    chaser_inertia = chaser_rotation @ np.diag(config.chaser.inertia_kg_m2) @ chaser_rotation.T
    target_inertia = target_rotation @ np.diag(config.target.inertia_kg_m2) @ target_rotation.T
    mass, center, inertia = combine_rigid_bodies(
        (
            RigidBodyProperties(config.chaser.mass_kg, chaser_center, chaser_inertia),
            RigidBodyProperties(config.target.mass_kg, target_center, target_inertia),
        )
    )
    moments, axes = np.linalg.eigh(inertia)
    return CombinedVehicleProperties(
        total_mass_kg=mass,
        center_of_mass_reference_m=center,
        inertia_about_com_reference_kg_m2=inertia,
        principal_moments_kg_m2=np.asarray(moments),
        principal_axes_reference=np.asarray(axes),
        chaser_center_reference_m=chaser_center,
        target_center_reference_m=target_center,
        docking_interface_reference_m=target_port,
    )


def applied_wrench_response(
    properties: CombinedVehicleProperties,
    force_reference_n: FloatArray,
    torque_at_application_point_reference_n_m: FloatArray,
    application_point_reference_m: FloatArray,
) -> WrenchResponse:
    """Return stack acceleration including moment-arm translation/rotation coupling."""
    force = np.asarray(force_reference_n, dtype=np.float64)
    torque = np.asarray(torque_at_application_point_reference_n_m, dtype=np.float64)
    point = np.asarray(application_point_reference_m, dtype=np.float64)
    if force.shape != (3,) or torque.shape != (3,) or point.shape != (3,):
        raise ValueError("force, torque, and application point must have shape (3,)")
    induced = np.cross(point - properties.center_of_mass_reference_m, force)
    angular = np.linalg.solve(properties.inertia_about_com_reference_kg_m2, torque + induced)
    return WrenchResponse(
        linear_acceleration_reference_m_s2=np.asarray(force / properties.total_mass_kg),
        angular_acceleration_reference_rad_s2=np.asarray(angular),
        induced_torque_reference_n_m=np.asarray(induced),
    )
