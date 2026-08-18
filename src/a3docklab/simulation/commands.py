"""Versioned driver intent and deterministic safety-arbitration contracts."""

from __future__ import annotations

import math
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from a3docklab.config import SimulationConfig


class DriverKind(StrEnum):
    AUTOPILOT = "autopilot"
    HUMAN = "human"
    MODEL = "model"


class IntentMode(StrEnum):
    AUTOPILOT = "autopilot"
    VELOCITY = "velocity"
    HOLD = "hold"
    RETREAT = "retreat"
    ABORT = "abort"
    CAPTURE = "capture"


class DecisionStatus(StrEnum):
    ACCEPTED = "accepted"
    LIMITED = "limited"
    SUBSTITUTED = "substituted"
    REJECTED = "rejected"


Vector3 = tuple[float, float, float]


class ControlIntent(BaseModel):
    """Driver request for one integration interval."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    command_id: str = Field(min_length=1)
    driver_id: str = Field(min_length=1)
    driver_kind: DriverKind
    issued_at_s: float = Field(ge=0.0)
    valid_for_s: float = Field(default=1.0, gt=0.0)
    mode: IntentMode
    desired_velocity_m_s: Vector3 | None = None
    desired_torque_n_m: Vector3 | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> ControlIntent:
        values = [
            *(self.desired_velocity_m_s or ()),
            *(self.desired_torque_n_m or ()),
        ]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("control vectors must contain finite values")
        if self.mode == IntentMode.VELOCITY and self.desired_velocity_m_s is None:
            raise ValueError("velocity intent requires desired_velocity_m_s")
        return self


class SimulationObservation(BaseModel):
    """Safety-relevant state exposed to a driver before command arbitration."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    run_id: str
    time_s: float
    phase: str
    position_m: Vector3
    velocity_m_s: Vector3
    fuel_mass_kg: float
    closing_rate_m_s: float
    closing_rate_limit_m_s: float
    corridor_margin_m: float
    keep_out_margin_m: float
    capture_eligible: bool


class CommandDecision(BaseModel):
    """Auditable command selected by the safety arbiter."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    command_id: str
    driver_id: str
    driver_kind: DriverKind
    requested_mode: IntentMode
    status: DecisionStatus
    reason: str
    requested_velocity_m_s: Vector3
    executed_velocity_m_s: Vector3
    requested_torque_n_m: Vector3
    executed_torque_n_m: Vector3 | None = None


def _vector(values: np.ndarray | Vector3) -> Vector3:
    array = np.asarray(values, dtype=np.float64)
    return float(array[0]), float(array[1]), float(array[2])


class CommandArbiter:
    """Fail-closed deterministic arbiter between drivers and vehicle control."""

    def __init__(self, config: SimulationConfig, authorized_driver_id: str | None = None) -> None:
        self.config = config
        self.authorized_driver_id = authorized_driver_id

    def decide(
        self,
        observation: SimulationObservation,
        autopilot_velocity_m_s: np.ndarray,
        intent: ControlIntent | None,
    ) -> CommandDecision:
        autopilot = _vector(autopilot_velocity_m_s)
        zero = (0.0, 0.0, 0.0)
        if intent is None:
            return CommandDecision(
                command_id=f"autopilot-{observation.time_s:.9f}",
                driver_id="reference-autopilot",
                driver_kind=DriverKind.AUTOPILOT,
                requested_mode=IntentMode.AUTOPILOT,
                status=DecisionStatus.ACCEPTED,
                reason="reference_autopilot",
                requested_velocity_m_s=autopilot,
                executed_velocity_m_s=autopilot,
                requested_torque_n_m=zero,
            )

        requested = intent.desired_velocity_m_s or zero
        torque = intent.desired_torque_n_m or zero
        base = {
            "command_id": intent.command_id,
            "driver_id": intent.driver_id,
            "driver_kind": intent.driver_kind,
            "requested_mode": intent.mode,
            "requested_velocity_m_s": requested,
            "requested_torque_n_m": torque,
        }
        if self.authorized_driver_id is not None and intent.driver_id != self.authorized_driver_id:
            return CommandDecision(
                **base,
                status=DecisionStatus.REJECTED,
                reason="unauthorized_driver",
                executed_velocity_m_s=zero,
            )
        if intent.issued_at_s > observation.time_s + 1e-9:
            return CommandDecision(
                **base,
                status=DecisionStatus.REJECTED,
                reason="command_from_future",
                executed_velocity_m_s=zero,
            )
        if observation.time_s - intent.issued_at_s > intent.valid_for_s:
            return CommandDecision(
                **base,
                status=DecisionStatus.SUBSTITUTED,
                reason="stale_command_autopilot_fallback",
                executed_velocity_m_s=autopilot,
            )
        if intent.mode == IntentMode.AUTOPILOT:
            return CommandDecision(
                **base,
                status=DecisionStatus.ACCEPTED,
                reason="autopilot_requested",
                executed_velocity_m_s=autopilot,
            )
        if intent.mode == IntentMode.HOLD:
            requested = zero
        elif intent.mode in {IntentMode.RETREAT, IntentMode.ABORT}:
            position = np.asarray(observation.position_m, dtype=np.float64)
            norm = float(np.linalg.norm(position))
            direction = position / norm if norm > 1e-12 else np.array([1.0, 0.0, 0.0])
            requested = _vector(direction * self.config.safety.abort_retreat_rate_m_s)
        elif intent.mode == IntentMode.CAPTURE:
            if not observation.capture_eligible:
                return CommandDecision(
                    **base,
                    status=DecisionStatus.REJECTED,
                    reason="capture_gate_not_satisfied",
                    executed_velocity_m_s=zero,
                )
            requested = zero

        requested_array = np.asarray(requested, dtype=np.float64)
        position = np.asarray(observation.position_m, dtype=np.float64)
        range_m = float(np.linalg.norm(position))
        requested_closing_rate = (
            -float(np.dot(position, requested_array)) / range_m if range_m > 1e-12 else 0.0
        )
        if (
            observation.corridor_margin_m < 0.0 or observation.keep_out_margin_m < 0.0
        ) and requested_closing_rate > 0.0:
            direction = position / range_m if range_m > 1e-12 else np.array([1.0, 0.0, 0.0])
            retreat = _vector(direction * self.config.safety.abort_retreat_rate_m_s)
            return CommandDecision(
                **base,
                status=DecisionStatus.SUBSTITUTED,
                reason="safety_margin_retreat",
                executed_velocity_m_s=retreat,
            )

        status = DecisionStatus.ACCEPTED
        reasons: list[str] = []
        if requested_closing_rate > observation.closing_rate_limit_m_s:
            radial = position / range_m
            excess = requested_closing_rate - observation.closing_rate_limit_m_s
            requested_array = requested_array + excess * radial
            status = DecisionStatus.LIMITED
            reasons.append("closing_rate_limited")
        max_speed = self.config.guidance.far_closing_rate_m_s
        speed = float(np.linalg.norm(requested_array))
        if speed > max_speed:
            requested_array *= max_speed / speed
            status = DecisionStatus.LIMITED
            reasons.append("velocity_limited")

        executed_torque: Vector3 | None = None
        if intent.desired_torque_n_m is not None:
            torque_array = np.asarray(torque, dtype=np.float64)
            torque_limit = np.asarray(self.config.attitude.maximum_torque_n_m, dtype=np.float64)
            limited_torque = np.clip(torque_array, -torque_limit, torque_limit)
            executed_torque = _vector(limited_torque)
            if not np.array_equal(torque_array, limited_torque):
                status = DecisionStatus.LIMITED
                reasons.append("torque_limited")
        return CommandDecision(
            **base,
            status=status,
            reason=";".join(reasons) or "command_accepted",
            executed_velocity_m_s=_vector(requested_array),
            executed_torque_n_m=executed_torque,
        )
