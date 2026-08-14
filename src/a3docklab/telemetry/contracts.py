"""Versioned Phase 3 telemetry and bundle contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from a3docklab.faults.models import FaultWindow


class TelemetryConfig(BaseModel):
    schema_version: str = "3.0"
    truth_rate_hz: float = Field(default=10.0, gt=0.0)
    control_rate_hz: float = Field(default=10.0, gt=0.0)
    navigation_rate_hz: float = Field(default=10.0, gt=0.0)
    relative_sensor_rate_hz: float = Field(default=5.0, gt=0.0)
    communications_rate_hz: float = Field(default=2.0, gt=0.0)
    range_noise_sigma_m: float = Field(default=0.05, ge=0.0)
    position_noise_sigma_m: float = Field(default=0.03, ge=0.0)
    velocity_noise_sigma_m_s: float = Field(default=0.005, ge=0.0)
    estimator_process_acceleration_sigma_m_s2: float = Field(default=0.01, ge=0.0)
    estimator_initial_position_sigma_m: float = Field(default=1.0, gt=0.0)
    estimator_initial_velocity_sigma_m_s: float = Field(default=0.1, gt=0.0)
    estimator_nis_threshold: float = Field(default=12.592, gt=0.0)
    communications_latency_s: float = Field(default=0.1, ge=0.0)
    packet_loss_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    display_point_budget: int = Field(default=2000, gt=100)


class FaultConfig(BaseModel):
    faults: list[FaultWindow] = Field(default_factory=list)


class StreamManifest(BaseModel):
    name: str
    path: str
    row_count: int = Field(ge=0)
    format: str
    time_column: str


class BundleManifest(BaseModel):
    schema_version: str
    run_id: str
    scenario_id: str
    created_at_utc: datetime
    configuration_hash: str
    terminal_phase: str
    streams: list[StreamManifest]
    feature_allowlist_path: str

    @model_validator(mode="after")
    def validate_stream_names(self) -> BundleManifest:
        names = [stream.name for stream in self.streams]
        if len(names) != len(set(names)):
            raise ValueError("bundle stream names must be unique")
        return self


def load_telemetry_config(path: str | Path) -> TelemetryConfig:
    with Path(path).open(encoding="utf-8") as stream:
        return TelemetryConfig.model_validate(yaml.safe_load(stream))


def load_fault_config(path: str | Path | None) -> FaultConfig:
    if path is None:
        return FaultConfig()
    with Path(path).open(encoding="utf-8") as stream:
        return FaultConfig.model_validate(yaml.safe_load(stream))


def phase3_identity(
    scenario: str,
    simulation_hash: str,
    telemetry: TelemetryConfig,
    faults: FaultConfig,
) -> tuple[str, str]:
    """Return a stable ID/hash including telemetry and fault configuration."""
    payload = {
        "simulation_hash": simulation_hash,
        "telemetry": telemetry.model_dump(mode="json"),
        "faults": faults.model_dump(mode="json"),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"{scenario}-{digest[:12]}", digest
