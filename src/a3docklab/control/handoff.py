"""Exclusive controller-authority token and deterministic handoff protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray

from a3docklab.config import HandoffConfig, VehicleConfig

FloatArray = NDArray[np.float64]


class AuthorityOwner(StrEnum):
    ORION = "orion"
    TARGET = "target"


class HandoffState(StrEnum):
    INDEPENDENT = "independent"
    READINESS = "readiness"
    QUIET_PERIOD = "quiet_period"
    TRANSFER_PENDING = "transfer_pending"
    ACKNOWLEDGED = "acknowledged"
    ACTIVE = "active"
    ROLLBACK = "rollback"


@dataclass(frozen=True)
class HandoffInputs:
    state_valid: bool = True
    covariance_valid: bool = True
    clock_synchronized: bool = True
    phase_consistent: bool = True
    actuator_healthy: bool = True
    command_magnitude: float = 0.0
    shadow_command_delta: float = 0.0
    acknowledgement_received: bool = True
    authority_consistent: bool = True

    @property
    def ready(self) -> bool:
        return all(
            (
                self.state_valid,
                self.covariance_valid,
                self.clock_synchronized,
                self.phase_consistent,
                self.actuator_healthy,
                self.authority_consistent,
            )
        )


@dataclass(frozen=True)
class HandoffExchangePacket:
    schema_version: str
    sequence_number: int
    source_time_s: float
    frame_id: str
    mission_phase: str
    position_m: tuple[float, float, float]
    velocity_m_s: tuple[float, float, float]
    attitude_quaternion_wxyz: tuple[float, float, float, float]
    angular_rate_rad_s: tuple[float, float, float]
    covariance: FloatArray
    actuator_health_fraction: float


@dataclass(frozen=True)
class ExchangeValidation:
    valid: bool
    state_valid: bool
    covariance_valid: bool
    clock_synchronized: bool
    phase_consistent: bool
    frame_consistent: bool
    actuator_healthy: bool
    data_age_s: float
    reason: str


@dataclass(frozen=True)
class ShadowWrench:
    force_reference_n: FloatArray
    torque_reference_n_m: FloatArray

    @property
    def vector(self) -> FloatArray:
        return np.concatenate((self.force_reference_n, self.torque_reference_n_m))


def shadow_stack_wrench(
    combined_velocity_reference_m_s: FloatArray,
    combined_angular_rate_reference_rad_s: FloatArray,
    total_mass_kg: float,
    inertia_reference_kg_m2: FloatArray,
    controller_vehicle: VehicleConfig,
    config: HandoffConfig,
) -> ShadowWrench:
    """Compute one candidate owner's bounded docked-stack damping wrench."""
    velocity = np.asarray(combined_velocity_reference_m_s, dtype=np.float64)
    omega = np.asarray(combined_angular_rate_reference_rad_s, dtype=np.float64)
    inertia = np.asarray(inertia_reference_kg_m2, dtype=np.float64)
    if velocity.shape != (3,) or omega.shape != (3,) or inertia.shape != (3, 3):
        raise ValueError("shadow controller state and inertia have invalid shape")
    force = -total_mass_kg * config.shadow_velocity_gain_s_inv * velocity
    force = np.clip(
        force,
        -controller_vehicle.max_translation_thrust_n,
        controller_vehicle.max_translation_thrust_n,
    )
    torque = -config.shadow_rate_gain_s_inv * (inertia @ omega)
    torque_limit = controller_vehicle.max_translation_thrust_n * 0.5 * controller_vehicle.diameter_m
    torque = np.clip(torque, -torque_limit, torque_limit)
    return ShadowWrench(np.asarray(force), np.asarray(torque))


def command_delta_norm(first: ShadowWrench, second: ShadowWrench, lever_scale_m: float) -> float:
    """Return an equivalent-force norm for force/torque command continuity."""
    if lever_scale_m <= 0.0:
        raise ValueError("lever scale must be positive")
    difference = first.vector - second.vector
    weighted = np.concatenate((difference[:3], difference[3:] / lever_scale_m))
    return float(np.linalg.norm(weighted))


def authority_observation_valid(orion_active: bool, target_active: bool) -> bool:
    """Exactly one externally observed controller must claim authority."""
    return int(orion_active) + int(target_active) == 1


def validate_exchange_packet(
    packet: HandoffExchangePacket,
    *,
    receive_time_s: float,
    expected_phase: str,
    config: HandoffConfig,
) -> ExchangeValidation:
    """Validate a handoff packet without silently repairing invalid data."""
    state = np.asarray((*packet.position_m, *packet.velocity_m_s), dtype=np.float64)
    quaternion = np.asarray(packet.attitude_quaternion_wxyz, dtype=np.float64)
    rate = np.asarray(packet.angular_rate_rad_s, dtype=np.float64)
    covariance = np.asarray(packet.covariance, dtype=np.float64)
    state_valid = bool(
        packet.schema_version == "2.0"
        and packet.sequence_number >= 0
        and np.all(np.isfinite(state))
        and np.all(np.isfinite(quaternion))
        and np.all(np.isfinite(rate))
        and np.isclose(np.linalg.norm(quaternion), 1.0, atol=1e-9)
    )
    covariance_valid = bool(
        covariance.shape == (12, 12)
        and np.all(np.isfinite(covariance))
        and np.allclose(covariance, covariance.T, atol=1e-12)
        and np.min(np.linalg.eigvalsh(covariance)) >= -1e-12
    )
    data_age = receive_time_s - packet.source_time_s
    clock_synchronized = 0.0 <= data_age <= config.maximum_data_age_s
    phase_consistent = packet.mission_phase == expected_phase
    frame_consistent = packet.frame_id == config.required_frame_id
    actuator_healthy = 0.999 <= packet.actuator_health_fraction <= 1.0
    checks = (
        (state_valid, "state_invalid"),
        (covariance_valid, "covariance_invalid"),
        (clock_synchronized, "stale_or_future_data"),
        (phase_consistent, "phase_mismatch"),
        (frame_consistent, "frame_mismatch"),
        (actuator_healthy, "actuator_unhealthy"),
    )
    reason = next((reason for valid, reason in checks if not valid), "valid")
    return ExchangeValidation(
        valid=all(valid for valid, _ in checks),
        state_valid=state_valid and frame_consistent,
        covariance_valid=covariance_valid,
        clock_synchronized=clock_synchronized,
        phase_consistent=phase_consistent,
        frame_consistent=frame_consistent,
        actuator_healthy=actuator_healthy,
        data_age_s=float(data_age),
        reason=reason,
    )


@dataclass(frozen=True)
class HandoffUpdate:
    changed: bool
    previous_state: HandoffState
    state: HandoffState
    owner: AuthorityOwner
    reason: str


@dataclass
class AuthorityHandoff:
    desired_owner: AuthorityOwner
    config: HandoffConfig
    owner: AuthorityOwner = AuthorityOwner.ORION
    state: HandoffState = HandoffState.INDEPENDENT
    entered_at_s: float = 0.0

    def _transition(self, state: HandoffState, time_s: float, reason: str) -> HandoffUpdate:
        previous = self.state
        self.state = state
        self.entered_at_s = time_s
        if state == HandoffState.ACTIVE:
            self.owner = self.desired_owner
        return HandoffUpdate(previous != state, previous, state, self.owner, reason)

    def begin(self, time_s: float) -> HandoffUpdate:
        """Begin handoff only after a successful capture latch."""
        if self.state != HandoffState.INDEPENDENT:
            return HandoffUpdate(False, self.state, self.state, self.owner, "already_started")
        return self._transition(HandoffState.READINESS, time_s, "capture_latched")

    def fail_active_owner(
        self, time_s: float, reason: str = "active_owner_failure"
    ) -> HandoffUpdate:
        """Atomically recover an active failed owner to Orion authority."""
        if self.state != HandoffState.ACTIVE:
            return HandoffUpdate(False, self.state, self.state, self.owner, "not_active")
        update = self._transition(HandoffState.ROLLBACK, time_s, reason)
        self.owner = AuthorityOwner.ORION
        return HandoffUpdate(
            update.changed, update.previous_state, update.state, self.owner, reason
        )

    def advance(self, time_s: float, inputs: HandoffInputs) -> HandoffUpdate:
        """Advance one deterministic protocol step while preserving one owner."""
        if self.state in {HandoffState.INDEPENDENT, HandoffState.ACTIVE, HandoffState.ROLLBACK}:
            return HandoffUpdate(False, self.state, self.state, self.owner, "stable")
        if not inputs.authority_consistent:
            return self._transition(HandoffState.ROLLBACK, time_s, "authority_invariant_violation")
        if not inputs.ready:
            return self._transition(HandoffState.ROLLBACK, time_s, "readiness_lost")
        if self.state == HandoffState.READINESS:
            return self._transition(HandoffState.QUIET_PERIOD, time_s, "readiness_confirmed")
        if self.state == HandoffState.QUIET_PERIOD:
            if inputs.command_magnitude > self.config.quiet_command_limit:
                self.entered_at_s = time_s
                return HandoffUpdate(
                    False, self.state, self.state, self.owner, "quiet_period_reset"
                )
            if time_s - self.entered_at_s >= self.config.quiet_period_s:
                return self._transition(
                    HandoffState.TRANSFER_PENDING, time_s, "quiet_period_complete"
                )
            return HandoffUpdate(False, self.state, self.state, self.owner, "quiet_period_running")
        if self.state == HandoffState.TRANSFER_PENDING:
            if time_s - self.entered_at_s > self.config.acknowledgement_timeout_s:
                return self._transition(HandoffState.ROLLBACK, time_s, "acknowledgement_timeout")
            if inputs.shadow_command_delta > self.config.maximum_command_discontinuity:
                return self._transition(HandoffState.ROLLBACK, time_s, "shadow_command_mismatch")
            if inputs.acknowledgement_received:
                return self._transition(HandoffState.ACKNOWLEDGED, time_s, "transfer_acknowledged")
            return HandoffUpdate(
                False, self.state, self.state, self.owner, "awaiting_acknowledgement"
            )
        if self.state == HandoffState.ACKNOWLEDGED:
            return self._transition(HandoffState.ACTIVE, time_s, "authority_activated")
        raise RuntimeError(f"unhandled handoff state {self.state}")
