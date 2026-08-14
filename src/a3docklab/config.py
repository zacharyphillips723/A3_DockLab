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


class DockingPortConfig(BaseModel):
    position_body_m: tuple[float, float, float]
    outward_normal_body: tuple[float, float, float]
    up_body: tuple[float, float, float]

    @model_validator(mode="after")
    def validate_axes(self) -> DockingPortConfig:
        normal_norm = math.sqrt(sum(value * value for value in self.outward_normal_body))
        up_norm = math.sqrt(sum(value * value for value in self.up_body))
        dot = sum(
            normal * up for normal, up in zip(self.outward_normal_body, self.up_body, strict=True)
        )
        if not math.isclose(normal_norm, 1.0, abs_tol=1e-9):
            raise ValueError("outward_normal_body must be a unit vector")
        if not math.isclose(up_norm, 1.0, abs_tol=1e-9):
            raise ValueError("up_body must be a unit vector")
        if not math.isclose(dot, 0.0, abs_tol=1e-9):
            raise ValueError("docking-port normal and up axes must be orthogonal")
        return self


class DockingConfig(BaseModel):
    geometry: Literal["side", "nose_to_nose"]
    capture_distance_m: float = Field(gt=0.0)
    max_closing_rate_m_s: float = Field(gt=0.0)
    max_lateral_offset_m: float = Field(gt=0.0)
    max_angular_error_deg: float = Field(gt=0.0)
    max_clocking_error_deg: float = Field(default=2.0, gt=0.0)
    docked_controller: Literal["orion", "target"]
    chaser_port: DockingPortConfig
    target_port: DockingPortConfig


class GuidanceConfig(BaseModel):
    docking_axis: Literal["x", "y", "z"] = "y"
    approach_sign: Literal[-1, 1] = -1
    hold_points_m: tuple[float, float] = (250.0, 30.0)
    hold_duration_s: float = Field(default=10.0, ge=0.0)
    far_closing_rate_m_s: float = Field(default=0.8, gt=0.0)
    proximity_closing_rate_m_s: float = Field(default=0.25, gt=0.0)
    final_closing_rate_m_s: float = Field(default=0.04, gt=0.0)

    @model_validator(mode="after")
    def validate_hold_points(self) -> GuidanceConfig:
        outer, inner = self.hold_points_m
        if not outer > inner > 0.0:
            raise ValueError("hold_points_m must be positive and ordered outer to inner")
        return self


class ControllerConfig(BaseModel):
    velocity_gain_s_inv: float = Field(default=0.08, gt=0.0)
    lateral_position_gain_s2_inv: float = Field(default=0.0005, ge=0.0)
    minimum_impulse_s: float = Field(default=0.05, ge=0.0)
    velocity_deadband_m_s: float = Field(default=0.002, ge=0.0)


class AttitudeConfig(BaseModel):
    chaser_initial_quaternion_wxyz: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    target_quaternion_wxyz: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    chaser_initial_rate_rad_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    target_rate_rad_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    proportional_gain_n_m_per_rad: tuple[float, float, float] = (500.0, 500.0, 500.0)
    derivative_gain_n_m_s_per_rad: tuple[float, float, float] = (5000.0, 5000.0, 5000.0)
    maximum_torque_n_m: tuple[float, float, float] = (100.0, 100.0, 100.0)
    control_enabled: bool = True

    @model_validator(mode="after")
    def validate_attitude(self) -> AttitudeConfig:
        for label, quaternion in (
            ("chaser", self.chaser_initial_quaternion_wxyz),
            ("target", self.target_quaternion_wxyz),
        ):
            norm = math.sqrt(sum(value * value for value in quaternion))
            if not math.isclose(norm, 1.0, abs_tol=1e-9):
                raise ValueError(f"{label} attitude quaternion must have unit norm")
        for label, values in (
            ("proportional gains", self.proportional_gain_n_m_per_rad),
            ("derivative gains", self.derivative_gain_n_m_s_per_rad),
            ("torque limits", self.maximum_torque_n_m),
        ):
            if any(value <= 0.0 for value in values):
                raise ValueError(f"all {label} must be positive")
        return self


class HandoffConfig(BaseModel):
    quiet_period_s: float = Field(default=2.0, ge=0.0)
    quiet_command_limit: float = Field(default=5.0, ge=0.0)
    acknowledgement_timeout_s: float = Field(default=5.0, gt=0.0)
    maximum_command_discontinuity: float = Field(default=10.0, ge=0.0)
    maximum_data_age_s: float = Field(default=0.5, gt=0.0)
    required_frame_id: str = "target_lvlh"
    injected_fault: Literal[
        "none",
        "stale_data",
        "frame_mismatch",
        "lost_acknowledgement",
        "actuator_unhealthy",
        "duplicated_authority",
        "lost_authority",
        "shadow_command_mismatch",
        "active_owner_failure",
    ] = "none"
    shadow_velocity_gain_s_inv: float = Field(default=0.002, gt=0.0)
    shadow_rate_gain_s_inv: float = Field(default=0.02, gt=0.0)
    post_handoff_control_duration_s: float = Field(default=60.0, gt=0.0)
    active_failure_delay_s: float = Field(default=5.0, ge=0.0)


class SafetyConfig(BaseModel):
    corridor_half_angle_deg: float = Field(default=10.0, gt=0.0, lt=90.0)
    corridor_min_radius_m: float = Field(default=0.25, ge=0.0)
    keep_out_radius_m: float = Field(default=10.0, gt=0.0)
    closing_rate_margin_m_s: float = Field(default=0.05, ge=0.0)
    abort_retreat_rate_m_s: float = Field(default=0.2, gt=0.0)
    terminal_alignment_gate_range_m: float = Field(default=1.0, gt=0.0)


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
    guidance: GuidanceConfig = GuidanceConfig()
    controller: ControllerConfig = ControllerConfig()
    attitude: AttitudeConfig = AttitudeConfig()
    handoff: HandoffConfig = HandoffConfig()
    safety: SafetyConfig = SafetyConfig()
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
