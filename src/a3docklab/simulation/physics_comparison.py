"""CW numerical checks and nonlinear two-body validity analysis."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp  # type: ignore[import-untyped]

from a3docklab.config import SimulationConfig
from a3docklab.dynamics.cw import derivative, mean_motion, propagate
from a3docklab.dynamics.frames import eci_relative_to_lvlh, lvlh_relative_to_eci
from a3docklab.dynamics.two_body import propagate_two_body


@dataclass(frozen=True)
class ComparisonSummary:
    duration_s: float
    initial_separation_m: float
    maximum_cw_numerical_error_m: float
    maximum_two_body_position_error_m: float
    terminal_two_body_position_error_m: float


def circular_target_state(config: SimulationConfig) -> np.ndarray:
    radius = config.orbit.semi_major_axis_m
    speed = np.sqrt(config.orbit.earth_mu_m3_s2 / radius)
    return np.array([radius, 0.0, 0.0, 0.0, speed, 0.0], dtype=np.float64)


def compare_cw_and_two_body(
    config: SimulationConfig,
    *,
    duration_s: float,
    step_s: float,
    relative_state0: np.ndarray | None = None,
    rtol: float = 1e-10,
    atol: float = 1e-12,
) -> tuple[pd.DataFrame, ComparisonSummary]:
    initial_relative = np.asarray(
        config.initial_relative_state_m_mps if relative_state0 is None else relative_state0,
        dtype=np.float64,
    )
    times = np.arange(0.0, duration_s + 0.5 * step_s, step_s, dtype=np.float64)
    n_rad_s = mean_motion(config.orbit.earth_mu_m3_s2, config.orbit.semi_major_axis_m)
    analytic = np.vstack([propagate(initial_relative, n_rad_s, float(time)) for time in times])
    numeric_solution = solve_ivp(
        lambda _time, state: derivative(state, n_rad_s),
        (0.0, duration_s),
        initial_relative,
        t_eval=times,
        method="DOP853",
        rtol=rtol,
        atol=atol,
    )
    if not numeric_solution.success:
        raise RuntimeError(f"numerical CW propagation failed: {numeric_solution.message}")
    numerical = np.asarray(numeric_solution.y.T, dtype=np.float64)

    target0 = circular_target_state(config)
    chaser0 = lvlh_relative_to_eci(target0, initial_relative)
    target_states = propagate_two_body(
        target0, times, config.orbit.earth_mu_m3_s2, rtol=rtol, atol=atol
    )
    chaser_states = propagate_two_body(
        chaser0, times, config.orbit.earth_mu_m3_s2, rtol=rtol, atol=atol
    )
    nonlinear = np.vstack(
        [
            eci_relative_to_lvlh(target, chaser)
            for target, chaser in zip(target_states, chaser_states, strict=True)
        ]
    )
    cw_numeric_error = np.linalg.norm(analytic[:, :3] - numerical[:, :3], axis=1)
    nonlinear_error = np.linalg.norm(analytic[:, :3] - nonlinear[:, :3], axis=1)
    frame = pd.DataFrame(
        {
            "time_s": times,
            "cw_x_m": analytic[:, 0],
            "cw_y_m": analytic[:, 1],
            "cw_z_m": analytic[:, 2],
            "two_body_x_m": nonlinear[:, 0],
            "two_body_y_m": nonlinear[:, 1],
            "two_body_z_m": nonlinear[:, 2],
            "cw_numerical_error_m": cw_numeric_error,
            "two_body_position_error_m": nonlinear_error,
        }
    )
    summary = ComparisonSummary(
        duration_s=duration_s,
        initial_separation_m=float(np.linalg.norm(initial_relative[:3])),
        maximum_cw_numerical_error_m=float(cw_numeric_error.max()),
        maximum_two_body_position_error_m=float(nonlinear_error.max()),
        terminal_two_body_position_error_m=float(nonlinear_error[-1]),
    )
    return frame, summary


def validity_envelope(
    config: SimulationConfig,
    separations_m: tuple[float, ...],
    durations_s: tuple[float, ...],
    *,
    step_s: float = 10.0,
) -> pd.DataFrame:
    rows: list[dict[str, float]] = []
    for separation in separations_m:
        initial = np.array([0.0, -separation, 0.0, 0.0, 0.0, 0.0], dtype=np.float64)
        for duration in durations_s:
            _, summary = compare_cw_and_two_body(
                config,
                duration_s=duration,
                step_s=min(step_s, duration),
                relative_state0=initial,
            )
            rows.append(
                {
                    "initial_separation_m": separation,
                    "duration_s": duration,
                    "maximum_position_error_m": summary.maximum_two_body_position_error_m,
                    "terminal_position_error_m": summary.terminal_two_body_position_error_m,
                }
            )
    return pd.DataFrame(rows)
