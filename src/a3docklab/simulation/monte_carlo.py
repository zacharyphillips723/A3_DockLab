"""Reproducible local Monte Carlo sampling, execution, and risk summaries."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, Field, model_validator

from a3docklab.config import SimulationConfig
from a3docklab.faults.models import FaultWindow
from a3docklab.simulation.engine import SimulationResult, run_controlled, summarize
from a3docklab.telemetry.contracts import FaultConfig, TelemetryConfig
from a3docklab.telemetry.generator import generate_streams


class ParameterDistribution(BaseModel):
    name: Literal[
        "initial_x_m",
        "initial_y_m",
        "initial_z_m",
        "initial_vx_m_s",
        "initial_vy_m_s",
        "initial_vz_m_s",
        "initial_x_offset_m",
        "initial_y_offset_m",
        "initial_z_offset_m",
        "initial_vx_offset_m_s",
        "initial_vy_offset_m_s",
        "initial_vz_offset_m_s",
        "chaser_mass_scale",
        "chaser_thrust_scale",
        "target_mass_scale",
        "target_thrust_scale",
    ]
    mean: float
    standard_deviation: float = Field(gt=0.0)
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def validate_bounds(self) -> ParameterDistribution:
        if not self.minimum <= self.mean <= self.maximum:
            raise ValueError("distribution mean must lie inside bounds")
        return self


class FaultDistribution(BaseModel):
    name: Literal[
        "navigation_drift",
        "thruster_underperformance",
        "communications_latency",
        "stale_data",
        "frame_mismatch",
        "lost_acknowledgement",
        "actuator_unhealthy",
        "duplicated_authority",
        "lost_authority",
        "shadow_command_mismatch",
        "active_owner_failure",
    ]
    probability: float = Field(ge=0.0, le=1.0)
    severity: float = Field(default=1.0, gt=0.0)
    start_fraction: float = Field(default=0.5, ge=0.0, le=1.0)
    duration_fraction: float = Field(default=0.1, gt=0.0, le=1.0)


class MonteCarloConfig(BaseModel):
    schema_version: str = "1.1"
    sample_count: int = Field(default=100, gt=0)
    random_seed: int = 73_031
    parameters: tuple[ParameterDistribution, ...]
    correlation_matrix: tuple[tuple[float, ...], ...]
    faults: tuple[FaultDistribution, ...] = ()

    @model_validator(mode="after")
    def validate_correlation(self) -> MonteCarloConfig:
        count = len(self.parameters)
        matrix = np.asarray(self.correlation_matrix, dtype=np.float64)
        if matrix.shape != (count, count):
            raise ValueError("correlation matrix dimension must match parameter count")
        if not np.allclose(matrix, matrix.T, atol=1e-12):
            raise ValueError("correlation matrix must be symmetric")
        if not np.allclose(np.diag(matrix), 1.0, atol=1e-12):
            raise ValueError("correlation matrix diagonal must equal one")
        if np.min(np.linalg.eigvalsh(matrix)) < -1e-12:
            raise ValueError("correlation matrix must be positive semidefinite")
        return self


@dataclass(frozen=True)
class EnsembleResult:
    manifest: dict[str, object]
    runs: pd.DataFrame
    convergence: pd.DataFrame
    risk_summary: dict[str, float | int]


def load_monte_carlo_config(path: str | Path) -> MonteCarloConfig:
    with Path(path).open(encoding="utf-8") as stream:
        return MonteCarloConfig.model_validate(yaml.safe_load(stream))


def sample_parameters(config: MonteCarloConfig) -> pd.DataFrame:
    """Draw bounded correlated normal samples with deterministic child seeds."""
    correlation = np.asarray(config.correlation_matrix, dtype=np.float64)
    eigenvalues, eigenvectors = np.linalg.eigh(correlation)
    root = eigenvectors @ np.diag(np.sqrt(np.maximum(eigenvalues, 0.0)))
    seed_sequence = np.random.SeedSequence(config.random_seed)
    rows: list[dict[str, float | int | str]] = []
    for index, child_seed in enumerate(seed_sequence.spawn(config.sample_count)):
        rng = np.random.default_rng(child_seed)
        correlated = root @ rng.standard_normal(len(config.parameters))
        row: dict[str, float | int | str] = {
            "sample_index": index,
            "sample_seed": int(child_seed.generate_state(1, dtype=np.uint32)[0]),
        }
        for parameter, standard_value in zip(config.parameters, correlated, strict=True):
            value = parameter.mean + parameter.standard_deviation * standard_value
            row[parameter.name] = float(np.clip(value, parameter.minimum, parameter.maximum))
        sampled_faults = [fault.name for fault in config.faults if rng.random() < fault.probability]
        row["sampled_faults"] = json.dumps(sampled_faults, separators=(",", ":"))
        rows.append(row)
    return pd.DataFrame(rows)


def _apply_sample(base: SimulationConfig, row: pd.Series) -> SimulationConfig:
    scenario = base.model_copy(deep=True)
    state = list(scenario.initial_relative_state_m_mps)
    state_names = (
        "initial_x_m",
        "initial_y_m",
        "initial_z_m",
        "initial_vx_m_s",
        "initial_vy_m_s",
        "initial_vz_m_s",
    )
    for index, name in enumerate(state_names):
        if name in row:
            state[index] = float(row[name])
    offset_names = (
        "initial_x_offset_m",
        "initial_y_offset_m",
        "initial_z_offset_m",
        "initial_vx_offset_m_s",
        "initial_vy_offset_m_s",
        "initial_vz_offset_m_s",
    )
    for index, name in enumerate(offset_names):
        if name in row:
            state[index] += float(row[name])
    scenario.initial_relative_state_m_mps = tuple(state)  # type: ignore[assignment]
    for name, vehicle, attribute in (
        ("chaser_mass_scale", scenario.chaser, "mass_kg"),
        ("chaser_thrust_scale", scenario.chaser, "max_translation_thrust_n"),
        ("target_mass_scale", scenario.target, "mass_kg"),
        ("target_thrust_scale", scenario.target, "max_translation_thrust_n"),
    ):
        if name in row:
            setattr(vehicle, attribute, getattr(vehicle, attribute) * float(row[name]))
    scenario.random_seed = int(row["sample_seed"])
    return scenario


def _fault_config(
    config: MonteCarloConfig, sampled_names: list[str], duration_s: float
) -> FaultConfig:
    telemetry_names = {
        "navigation_drift",
        "thruster_underperformance",
        "communications_latency",
    }
    windows = [
        FaultWindow.model_validate(
            {
                "name": fault.name,
                "start_s": fault.start_fraction * duration_s,
                "duration_s": max(1e-6, fault.duration_fraction * duration_s),
                "severity": fault.severity,
            }
        )
        for fault in config.faults
        if fault.name in sampled_names and fault.name in telemetry_names
    ]
    return FaultConfig(faults=windows)


def _estimator_metrics(
    result: SimulationResult,
    telemetry_config: TelemetryConfig,
    fault_config: FaultConfig,
    random_seed: int,
) -> dict[str, float]:
    streams = generate_streams(
        result,
        telemetry_config,
        fault_config,
        random_seed=random_seed,
    )
    estimates = streams.navigation_estimates
    measured = estimates.loc[estimates["measurement_used"]]
    source = result.telemetry
    times = estimates["event_time_ns"].to_numpy(dtype=np.float64) / 1_000_000_000
    errors = []
    for estimate_column, truth_column in zip(
        (
            "estimate_x_m",
            "estimate_y_m",
            "estimate_z_m",
            "estimate_vx_m_s",
            "estimate_vy_m_s",
            "estimate_vz_m_s",
        ),
        ("x_m", "y_m", "z_m", "vx_m_s", "vy_m_s", "vz_m_s"),
        strict=True,
    ):
        truth = np.interp(times, source["time_s"], source[truth_column])
        errors.append(estimates[estimate_column].to_numpy(dtype=np.float64) - truth)
    error_matrix = np.column_stack(errors)
    return {
        "estimator_position_rmse_m": float(np.sqrt(np.mean(error_matrix[:, :3] ** 2))),
        "estimator_velocity_rmse_m_s": float(np.sqrt(np.mean(error_matrix[:, 3:] ** 2))),
        "nis_consistent_fraction": float(measured["innovation_consistent"].mean()),
        "median_nis": float(measured["normalized_innovation_squared"].median()),
        "p95_nis": float(measured["normalized_innovation_squared"].quantile(0.95)),
        "maximum_covariance_trace": float(estimates["covariance_trace"].max()),
    }


def run_ensemble(
    base: SimulationConfig,
    config: MonteCarloConfig,
    telemetry_config: TelemetryConfig | None = None,
) -> EnsembleResult:
    samples = sample_parameters(config)
    run_rows: list[dict[str, object]] = []
    for _, sample in samples.iterrows():
        scenario = _apply_sample(base, sample)
        sampled_faults = json.loads(str(sample["sampled_faults"]))
        handoff_faults = {
            fault.name
            for fault in config.faults
            if fault.name
            not in {"navigation_drift", "thruster_underperformance", "communications_latency"}
        }
        selected_handoff_fault = next(
            (name for name in sampled_faults if name in handoff_faults), "none"
        )
        scenario.handoff.injected_fault = selected_handoff_fault  # type: ignore[assignment]
        result = run_controlled(scenario)
        summary = summarize(result)
        telemetry = result.telemetry
        capture_rows = telemetry.loc[telemetry["capture_latched"]]
        contact = capture_rows.iloc[0] if not capture_rows.empty else None
        row: dict[str, object] = {str(key): value for key, value in sample.to_dict().items()}
        row.update(
            {
                "run_id": str(telemetry["run_id"].iat[0]),
                "scenario": base.name,
                "terminal_phase": summary.terminal_phase,
                "capture_success": summary.terminal_phase == "complete",
                "abort": summary.terminal_phase == "abort",
                "elapsed_time_s": summary.elapsed_time_s,
                "propellant_used_kg": summary.propellant_used_kg,
                "closest_approach_m": summary.closest_approach_m,
                "contact_closing_rate_m_s": (
                    float(contact["closing_rate_m_s"]) if contact is not None else np.nan
                ),
                "contact_lateral_offset_m": (
                    float(contact["port_lateral_offset_m"]) if contact is not None else np.nan
                ),
                "contact_angular_error_deg": (
                    float(contact["port_angular_error_deg"]) if contact is not None else np.nan
                ),
                "capture_dissipated_energy_j": (
                    float(contact["capture_dissipated_energy_j"]) if contact is not None else np.nan
                ),
                "warning_count": summary.warning_count,
                "maximum_lateral_offset_m": float(telemetry["port_lateral_offset_m"].max()),
                "maximum_angular_error_deg": float(telemetry["port_angular_error_deg"].max()),
                "allocation_saturation_fraction": float(telemetry["allocation_saturated"].mean()),
                "handoff_rollback": bool(telemetry["handoff_state"].eq("rollback").any()),
                "final_authority": str(telemetry["controller_authority"].iat[-1]),
                "faulted": bool(sampled_faults),
                "sampled_fault_count": len(sampled_faults),
            }
        )
        if telemetry_config is not None:
            row.update(
                _estimator_metrics(
                    result,
                    telemetry_config,
                    _fault_config(config, sampled_faults, scenario.duration_s),
                    scenario.random_seed,
                )
            )
        run_rows.append(row)
    runs = pd.DataFrame(run_rows)
    convergence_rows: list[dict[str, float | int]] = []
    capture = runs["capture_success"].to_numpy(dtype=np.float64)
    abort = runs["abort"].to_numpy(dtype=np.float64)
    for count in range(1, len(runs) + 1):
        capture_rate = float(capture[:count].mean())
        convergence_rows.append(
            {
                "sample_count": count,
                "capture_rate": capture_rate,
                "abort_rate": float(abort[:count].mean()),
                "capture_rate_standard_error": float(
                    np.sqrt(capture_rate * (1.0 - capture_rate) / count)
                ),
                "mean_propellant_used_kg": float(runs["propellant_used_kg"].iloc[:count].mean()),
            }
        )
    convergence = pd.DataFrame(convergence_rows)
    risk_summary: dict[str, float | int] = {
        "sample_count": len(runs),
        "capture_rate": float(runs["capture_success"].mean()),
        "abort_rate": float(runs["abort"].mean()),
        "handoff_rollback_rate": float(runs["handoff_rollback"].mean()),
        "mean_propellant_used_kg": float(runs["propellant_used_kg"].mean()),
        "p95_propellant_used_kg": float(runs["propellant_used_kg"].quantile(0.95)),
        "p05_closest_approach_m": float(runs["closest_approach_m"].quantile(0.05)),
        "p95_maximum_lateral_offset_m": float(runs["maximum_lateral_offset_m"].quantile(0.95)),
        "p95_maximum_angular_error_deg": float(runs["maximum_angular_error_deg"].quantile(0.95)),
        "faulted_run_fraction": float(runs["faulted"].mean()),
    }
    if telemetry_config is not None:
        risk_summary.update(
            {
                "p05_nis_consistent_fraction": float(
                    runs["nis_consistent_fraction"].quantile(0.05)
                ),
                "p95_estimator_position_rmse_m": float(
                    runs["estimator_position_rmse_m"].quantile(0.95)
                ),
                "p95_estimator_velocity_rmse_m_s": float(
                    runs["estimator_velocity_rmse_m_s"].quantile(0.95)
                ),
                "p95_maximum_covariance_trace": float(
                    runs["maximum_covariance_trace"].quantile(0.95)
                ),
            }
        )
    config_payload = config.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(config_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest: dict[str, object] = {
        "schema_version": config.schema_version,
        "ensemble_id": f"{base.name}-{digest[:12]}",
        "scenario": base.name,
        "random_seed": config.random_seed,
        "sample_count": config.sample_count,
        "parameter_order": [parameter.name for parameter in config.parameters],
        "configuration_sha256": digest,
        "execution_backend": "local",
        "partition_count": 1,
        "sample_seed_strategy": "numpy_seed_sequence_spawn",
        "ensemble_config": config_payload,
        "base_configuration": base.model_dump(mode="json"),
        "telemetry_configuration": (
            telemetry_config.model_dump(mode="json") if telemetry_config is not None else None
        ),
    }
    return EnsembleResult(manifest, runs, convergence, risk_summary)


def write_ensemble(root: str | Path, result: EnsembleResult) -> None:
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    result.runs.to_parquet(directory / "runs.parquet", index=False)
    result.convergence.to_parquet(directory / "convergence.parquet", index=False)
    (directory / "manifest.json").write_text(
        json.dumps(result.manifest, indent=2), encoding="utf-8"
    )
    (directory / "risk_summary.json").write_text(
        json.dumps(result.risk_summary, indent=2), encoding="utf-8"
    )
