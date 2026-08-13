"""Minimal deterministic simulation engine for the starter repository."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from a3docklab.config import SimulationConfig
from a3docklab.dynamics.cw import mean_motion, propagate
from a3docklab.run_metadata import deterministic_run_id


@dataclass(frozen=True)
class SimulationResult:
    telemetry: pd.DataFrame


def run_cw(config: SimulationConfig) -> SimulationResult:
    """Run an uncontrolled CW reference trajectory and emit truth telemetry."""
    n = mean_motion(config.orbit.earth_mu_m3_s2, config.orbit.semi_major_axis_m)
    initial = np.asarray(config.initial_relative_state_m_mps, dtype=np.float64)
    times = np.arange(0.0, config.duration_s + 0.5 * config.step_s, config.step_s)
    states = np.vstack([propagate(initial, n, float(time_s)) for time_s in times])
    range_m = np.linalg.norm(states[:, :3], axis=1)
    closing_rate_m_s = np.zeros_like(range_m)
    nonzero = range_m > 1e-12
    closing_rate_m_s[nonzero] = (
        -np.sum(states[nonzero, :3] * states[nonzero, 3:], axis=1) / range_m[nonzero]
    )

    telemetry = pd.DataFrame(
        {
            "run_id": deterministic_run_id(config),
            "time_s": times,
            "x_m": states[:, 0],
            "y_m": states[:, 1],
            "z_m": states[:, 2],
            "vx_m_s": states[:, 3],
            "vy_m_s": states[:, 4],
            "vz_m_s": states[:, 5],
            "range_m": range_m,
            "closing_rate_m_s": closing_rate_m_s,
            "scenario": config.name,
            "random_seed": config.random_seed,
            "fidelity": config.fidelity,
            "phase": "reference_propagation",
        }
    )
    return SimulationResult(telemetry=telemetry)
