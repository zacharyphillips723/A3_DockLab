"""Bounded CW translation controller and propellant accounting."""

from __future__ import annotations

import numpy as np

from a3docklab.config import ControllerConfig, VehicleConfig

STANDARD_GRAVITY_M_S2 = 9.80665


def commanded_acceleration(
    state: np.ndarray,
    desired_velocity_m_s: np.ndarray,
    n_rad_s: float,
    controller: ControllerConfig,
) -> np.ndarray:
    position = state[:3]
    velocity = state[3:]
    natural = np.array(
        [
            3 * n_rad_s**2 * position[0] + 2 * n_rad_s * velocity[1],
            -2 * n_rad_s * velocity[0],
            -(n_rad_s**2) * position[2],
        ],
        dtype=np.float64,
    )
    velocity_error = desired_velocity_m_s - velocity
    velocity_error[np.abs(velocity_error) < controller.velocity_deadband_m_s] = 0.0
    lateral = np.array([-position[0], 0.0, -position[2]], dtype=np.float64)
    return np.asarray(
        -natural
        + controller.velocity_gain_s_inv * velocity_error
        + controller.lateral_position_gain_s2_inv * lateral,
        dtype=np.float64,
    )


def allocate_force(
    acceleration_m_s2: np.ndarray,
    vehicle: VehicleConfig,
    controller: ControllerConfig,
    step_s: float,
) -> np.ndarray:
    force = np.asarray(acceleration_m_s2, dtype=np.float64) * vehicle.mass_kg
    magnitude = float(np.linalg.norm(force))
    if magnitude > vehicle.max_translation_thrust_n:
        force *= vehicle.max_translation_thrust_n / magnitude
        magnitude = vehicle.max_translation_thrust_n
    minimum_impulse_n_s = vehicle.max_translation_thrust_n * controller.minimum_impulse_s
    if 0.0 < magnitude * step_s < minimum_impulse_n_s:
        quantized_magnitude = min(vehicle.max_translation_thrust_n, minimum_impulse_n_s / step_s)
        force *= quantized_magnitude / magnitude
    return np.asarray(force, dtype=np.float64)


def propellant_used_kg(force_n: np.ndarray, duration_s: float, specific_impulse_s: float) -> float:
    impulse_n_s = float(np.linalg.norm(force_n)) * duration_s
    return impulse_n_s / (specific_impulse_s * STANDARD_GRAVITY_M_S2)
