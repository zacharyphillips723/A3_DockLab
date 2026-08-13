"""Validated configuration models for A3 DockLab."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class OrbitConfig(BaseModel):
    altitude_m: float = Field(gt=100_000.0)
    earth_mu_m3_s2: float = Field(default=3.986004418e14, gt=0.0)
    earth_radius_m: float = Field(default=6_378_137.0, gt=0.0)

    @property
    def semi_major_axis_m(self) -> float:
        return self.earth_radius_m + self.altitude_m


class VehicleConfig(BaseModel):
    name: str
    mass_kg: float = Field(gt=0.0)
    inertia_kg_m2: tuple[float, float, float]
    length_m: float = Field(gt=0.0)
    diameter_m: float = Field(gt=0.0)
    max_translation_thrust_n: float = Field(gt=0.0)
    specific_impulse_s: float = Field(gt=0.0)
    assumption_confidence: Literal["public", "derived", "placeholder"] = "placeholder"

    @model_validator(mode="after")
    def validate_inertia(self) -> VehicleConfig:
        if any(value <= 0.0 for value in self.inertia_kg_m2):
            raise ValueError("All principal moments of inertia must be positive")
        return self


class DockingConfig(BaseModel):
    geometry: Literal["side", "nose_to_nose"]
    capture_distance_m: float = Field(gt=0.0)
    max_closing_rate_m_s: float = Field(gt=0.0)
    max_lateral_offset_m: float = Field(gt=0.0)
    max_angular_error_deg: float = Field(gt=0.0)
    docked_controller: Literal["orion", "target"]


class SimulationConfig(BaseModel):
    name: str
    random_seed: int = 42
    duration_s: float = Field(gt=0.0)
    step_s: float = Field(gt=0.0)
    fidelity: Literal["cw", "two_body", "six_dof"] = "cw"
    orbit: OrbitConfig
    chaser: VehicleConfig
    target: VehicleConfig
    docking: DockingConfig
    initial_relative_state_m_mps: tuple[float, float, float, float, float, float]

    @model_validator(mode="after")
    def validate_simulation(self) -> SimulationConfig:
        if self.step_s > self.duration_s:
            raise ValueError("step_s must not exceed duration_s")
        if not all(math.isfinite(value) for value in self.initial_relative_state_m_mps):
            raise ValueError("initial_relative_state_m_mps must contain finite values")
        return self


def load_config(path: str | Path) -> SimulationConfig:
    """Load and validate a YAML scenario configuration."""
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    return SimulationConfig.model_validate(raw)
