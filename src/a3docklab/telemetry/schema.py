"""Telemetry schema used by simulation, storage, dashboards, and anomaly models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TelemetrySample(BaseModel):
    run_id: str
    scenario: str
    time_s: float = Field(ge=0.0)
    phase: str
    position_m: tuple[float, float, float]
    velocity_m_s: tuple[float, float, float]
    nav_position_m: tuple[float, float, float]
    nav_velocity_m_s: tuple[float, float, float]
    position_sigma_m: tuple[float, float, float]
    velocity_sigma_m_s: tuple[float, float, float]
    attitude_quaternion_wxyz: tuple[float, float, float, float]
    angular_rate_rad_s: tuple[float, float, float]
    commanded_force_n: tuple[float, float, float]
    actual_force_n: tuple[float, float, float]
    fuel_mass_kg: float = Field(ge=0.0)
    communications_age_s: float = Field(ge=0.0)
    channel_valid_mask: int = Field(ge=0)
    warning_mask: int = Field(ge=0)
    injected_fault: str | None = None
