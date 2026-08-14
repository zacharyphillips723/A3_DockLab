"""Bounded individual-thruster force and torque allocation.

The default layout is a symmetric engineering placeholder, not a published
vehicle thruster map. Thruster commands are duty fractions over one control
step and therefore remain nonnegative.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import lsq_linear  # type: ignore[import-untyped]

from a3docklab.config import ControllerConfig, VehicleConfig

STANDARD_GRAVITY_M_S2 = 9.80665

FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class Thruster:
    name: str
    position_body_m: tuple[float, float, float]
    direction_body: tuple[float, float, float]
    maximum_thrust_n: float


@dataclass(frozen=True)
class AllocationResult:
    duty_cycles: FloatArray
    achieved_force_body_n: FloatArray
    achieved_torque_body_n_m: FloatArray
    force_residual_n: FloatArray
    torque_residual_n_m: FloatArray
    saturated: bool
    minimum_impulse_active: bool
    active_thruster_count: int
    propellant_used_kg: float
    pending_duty_cycles: FloatArray


def default_thruster_layout(vehicle: VehicleConfig) -> tuple[Thruster, ...]:
    """Return a symmetric 24-jet layout scaled by vehicle envelope and authority."""
    axial = 0.4 * vehicle.length_m
    radial = 0.45 * vehicle.diameter_m
    per_jet = vehicle.max_translation_thrust_n / 4.0
    positions = (
        (axial, radial, 0.0),
        (axial, -radial, 0.0),
        (-axial, radial, 0.0),
        (-axial, -radial, 0.0),
    )
    directions = (
        (1.0, 0.0, 0.0),
        (-1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, -1.0, 0.0),
        (0.0, 0.0, 1.0),
        (0.0, 0.0, -1.0),
    )
    return tuple(
        Thruster(f"T{index + 1:02d}", position, direction, per_jet)
        for index, (position, direction) in enumerate(
            pair
            for direction in directions
            for pair in (
                (positions[0], direction),
                (positions[1], direction),
                (positions[2], direction),
                (positions[3], direction),
            )
        )
    )


def allocation_matrix(thrusters: tuple[Thruster, ...]) -> FloatArray:
    """Map duty fractions to body force and torque."""
    columns: list[FloatArray] = []
    for thruster in thrusters:
        direction = np.asarray(thruster.direction_body, dtype=np.float64)
        direction /= np.linalg.norm(direction)
        force = thruster.maximum_thrust_n * direction
        torque = np.cross(np.asarray(thruster.position_body_m, dtype=np.float64), force)
        columns.append(np.concatenate((force, torque)))
    return np.asarray(np.column_stack(columns), dtype=np.float64)


def allocate_wrench(
    requested_force_body_n: FloatArray,
    requested_torque_body_n_m: FloatArray,
    thrusters: tuple[Thruster, ...],
    controller: ControllerConfig,
    step_s: float,
    specific_impulse_s: float,
    health_factors: FloatArray | None = None,
    pending_duty_cycles: FloatArray | None = None,
) -> AllocationResult:
    """Allocate a six-axis wrench subject to jet health and duty bounds."""
    requested = np.concatenate(
        (
            np.asarray(requested_force_body_n, dtype=np.float64),
            np.asarray(requested_torque_body_n_m, dtype=np.float64),
        )
    )
    if requested.shape != (6,) or step_s <= 0.0 or specific_impulse_s <= 0.0 or not thrusters:
        raise ValueError(
            "requested wrench must have shape (6,), step must be positive, and jets required"
        )
    matrix = allocation_matrix(thrusters)
    health = (
        np.ones(len(thrusters), dtype=np.float64)
        if health_factors is None
        else np.asarray(health_factors, dtype=np.float64)
    )
    if health.shape != (len(thrusters),) or np.any((health < 0.0) | (health > 1.0)):
        raise ValueError("health factors must be in [0, 1] with one value per thruster")
    effective = matrix * health[None, :]
    lever_scale = max(
        np.linalg.norm(np.asarray(thruster.position_body_m)) for thruster in thrusters
    )
    weights = np.array([1.0, 1.0, 1.0, 1.0 / lever_scale, 1.0 / lever_scale, 1.0 / lever_scale])
    weighted_matrix = effective * weights[:, None]
    # A small command-effort penalty removes cancellation null spaces where
    # opposing jets could fire without improving the requested wrench.
    regularization = 1e-5 * max(float(np.linalg.norm(weighted_matrix, ord=2)), 1.0)
    objective_matrix = np.vstack((weighted_matrix, regularization * np.eye(len(thrusters))))
    objective_target = np.concatenate((requested * weights, np.zeros(len(thrusters))))
    solution = lsq_linear(
        objective_matrix,
        objective_target,
        bounds=(np.zeros(len(thrusters)), np.ones(len(thrusters))),
        lsmr_tol="auto",
    )
    duties = np.asarray(solution.x, dtype=np.float64)
    duties[duties < 1e-8] = 0.0
    minimum_duty = min(1.0, controller.minimum_impulse_s / step_s)
    quantized = (minimum_duty > 0.0) & (duties > 1e-9) & (duties < minimum_duty)
    pending = (
        np.zeros(len(thrusters), dtype=np.float64)
        if pending_duty_cycles is None
        else np.asarray(pending_duty_cycles, dtype=np.float64).copy()
    )
    if pending.shape != (len(thrusters),) or np.any(pending < 0.0):
        raise ValueError("pending duties must be nonnegative with one value per thruster")
    pending[quantized] += duties[quantized]
    duties[quantized] = 0.0
    pulse_ready = (minimum_duty > 0.0) & (pending >= minimum_duty)
    duties[pulse_ready] = minimum_duty
    pending[pulse_ready] -= minimum_duty
    duties[health == 0.0] = 0.0
    pending[health == 0.0] = 0.0
    achieved = effective @ duties
    residual = requested - achieved
    total_thrust_n = sum(
        thruster.maximum_thrust_n * health[index] * duties[index]
        for index, thruster in enumerate(thrusters)
    )
    return AllocationResult(
        duty_cycles=duties,
        achieved_force_body_n=np.asarray(achieved[:3]),
        achieved_torque_body_n_m=np.asarray(achieved[3:]),
        force_residual_n=np.asarray(residual[:3]),
        torque_residual_n_m=np.asarray(residual[3:]),
        saturated=bool(np.any(duties >= 1.0 - 1e-8)),
        minimum_impulse_active=bool(np.any(quantized)),
        active_thruster_count=int(np.count_nonzero(duties > 0.0)),
        propellant_used_kg=float(
            total_thrust_n * step_s / (specific_impulse_s * STANDARD_GRAVITY_M_S2)
        ),
        pending_duty_cycles=pending,
    )
