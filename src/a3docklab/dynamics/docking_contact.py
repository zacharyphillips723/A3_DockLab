"""Qualitative compliant contact and momentum-conserving capture latch.

The force law is intended for sensitivity and visualization, not structural
loads. The event-based latch projects two free rigid bodies onto one combined
rigid-body motion while preserving total linear and angular momentum.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from a3docklab.dynamics.combined_vehicle import CombinedVehicleProperties, RigidBodyProperties

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class ContactParameters:
    normal_stiffness_n_m: float = 20_000.0
    normal_damping_n_s_m: float = 2_000.0
    friction_coefficient: float = 0.2
    tangential_regularization_m_s: float = 0.01


@dataclass(frozen=True)
class ContactForce:
    force_on_chaser_reference_n: FloatArray
    normal_force_n: float
    tangential_force_n: float


@dataclass(frozen=True)
class CaptureLatchResult:
    combined_velocity_reference_m_s: FloatArray
    combined_angular_rate_reference_rad_s: FloatArray
    linear_momentum_before_kg_m_s: FloatArray
    linear_momentum_after_kg_m_s: FloatArray
    angular_momentum_before_kg_m2_s: FloatArray
    angular_momentum_after_kg_m2_s: FloatArray
    linear_momentum_residual_kg_m_s: float
    angular_momentum_residual_kg_m2_s: float
    kinetic_energy_before_j: float
    kinetic_energy_after_j: float
    dissipated_energy_j: float


def compliant_contact_force(
    penetration_m: float,
    penetration_rate_m_s: float,
    tangential_velocity_reference_m_s: FloatArray,
    contact_normal_reference: FloatArray,
    parameters: ContactParameters | None = None,
) -> ContactForce:
    """Return spring-damper normal force plus regularized Coulomb friction."""
    resolved_parameters = ContactParameters() if parameters is None else parameters
    tangent_velocity = np.asarray(tangential_velocity_reference_m_s, dtype=np.float64)
    normal = np.asarray(contact_normal_reference, dtype=np.float64)
    if tangent_velocity.shape != (3,) or normal.shape != (3,):
        raise ValueError("tangential velocity and normal must have shape (3,)")
    normal_norm = float(np.linalg.norm(normal))
    if normal_norm <= 0.0:
        raise ValueError("contact normal must have positive norm")
    normal /= normal_norm
    if penetration_m <= 0.0:
        return ContactForce(np.zeros(3), 0.0, 0.0)
    normal_force = max(
        0.0,
        resolved_parameters.normal_stiffness_n_m * penetration_m
        + resolved_parameters.normal_damping_n_s_m * penetration_rate_m_s,
    )
    tangent_speed = float(np.linalg.norm(tangent_velocity))
    tangent_force = (
        resolved_parameters.friction_coefficient
        * normal_force
        * np.tanh(tangent_speed / resolved_parameters.tangential_regularization_m_s)
    )
    friction = (
        np.zeros(3) if tangent_speed <= 0.0 else -tangent_force * tangent_velocity / tangent_speed
    )
    return ContactForce(
        force_on_chaser_reference_n=np.asarray(normal_force * normal + friction),
        normal_force_n=float(normal_force),
        tangential_force_n=float(tangent_force),
    )


def _kinetic_energy(body: RigidBodyProperties, velocity: FloatArray, omega: FloatArray) -> float:
    return float(
        0.5 * body.mass_kg * np.dot(velocity, velocity)
        + 0.5 * omega @ body.inertia_about_com_reference_kg_m2 @ omega
    )


def latch_capture(
    chaser: RigidBodyProperties,
    target: RigidBodyProperties,
    chaser_velocity_reference_m_s: FloatArray,
    target_velocity_reference_m_s: FloatArray,
    chaser_angular_rate_reference_rad_s: FloatArray,
    target_angular_rate_reference_rad_s: FloatArray,
    combined: CombinedVehicleProperties,
) -> CaptureLatchResult:
    """Project two bodies into one perfectly inelastic, momentum-conserving stack."""
    chaser_velocity = np.asarray(chaser_velocity_reference_m_s, dtype=np.float64)
    target_velocity = np.asarray(target_velocity_reference_m_s, dtype=np.float64)
    chaser_omega = np.asarray(chaser_angular_rate_reference_rad_s, dtype=np.float64)
    target_omega = np.asarray(target_angular_rate_reference_rad_s, dtype=np.float64)
    if any(
        value.shape != (3,)
        for value in (chaser_velocity, target_velocity, chaser_omega, target_omega)
    ):
        raise ValueError("all velocity and angular-rate vectors must have shape (3,)")
    linear_before = chaser.mass_kg * chaser_velocity + target.mass_kg * target_velocity
    combined_velocity = linear_before / combined.total_mass_kg
    chaser_arm = chaser.center_of_mass_reference_m - combined.center_of_mass_reference_m
    target_arm = target.center_of_mass_reference_m - combined.center_of_mass_reference_m
    angular_before = (
        chaser.inertia_about_com_reference_kg_m2 @ chaser_omega
        + np.cross(chaser_arm, chaser.mass_kg * chaser_velocity)
        + target.inertia_about_com_reference_kg_m2 @ target_omega
        + np.cross(target_arm, target.mass_kg * target_velocity)
    )
    combined_omega = np.linalg.solve(combined.inertia_about_com_reference_kg_m2, angular_before)
    linear_after = combined.total_mass_kg * combined_velocity
    angular_after = combined.inertia_about_com_reference_kg_m2 @ combined_omega
    energy_before = _kinetic_energy(chaser, chaser_velocity, chaser_omega) + _kinetic_energy(
        target, target_velocity, target_omega
    )
    energy_after = float(
        0.5 * combined.total_mass_kg * np.dot(combined_velocity, combined_velocity)
        + 0.5 * combined_omega @ combined.inertia_about_com_reference_kg_m2 @ combined_omega
    )
    return CaptureLatchResult(
        combined_velocity_reference_m_s=np.asarray(combined_velocity),
        combined_angular_rate_reference_rad_s=np.asarray(combined_omega),
        linear_momentum_before_kg_m_s=np.asarray(linear_before),
        linear_momentum_after_kg_m_s=np.asarray(linear_after),
        angular_momentum_before_kg_m2_s=np.asarray(angular_before),
        angular_momentum_after_kg_m2_s=np.asarray(angular_after),
        linear_momentum_residual_kg_m_s=float(np.linalg.norm(linear_after - linear_before)),
        angular_momentum_residual_kg_m2_s=float(np.linalg.norm(angular_after - angular_before)),
        kinetic_energy_before_j=energy_before,
        kinetic_energy_after_j=energy_after,
        dissipated_energy_j=max(0.0, energy_before - energy_after),
    )
