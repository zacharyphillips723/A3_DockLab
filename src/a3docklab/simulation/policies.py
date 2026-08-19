"""Versioned policy adapters for active and shadow simulation drivers."""

from __future__ import annotations

from typing import Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from a3docklab.config import SimulationConfig
from a3docklab.simulation.commands import (
    CommandArbiter,
    CommandDecision,
    ControlIntent,
    DriverKind,
    IntentMode,
    SimulationObservation,
)


class PolicyMetadata(BaseModel):
    """Stable provenance attached to every policy adapter."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    adapter_type: str = Field(min_length=1)
    observation_schema_version: str = "1.0"
    action_schema_version: str = "1.0"


class PolicyAdapter(Protocol):
    """A deterministic policy that proposes intent from an observation."""

    @property
    def metadata(self) -> PolicyMetadata: ...

    def propose(self, observation: SimulationObservation) -> ControlIntent: ...


class ReferenceAutopilotPolicy:
    """Expose the existing guidance controller through the policy contract."""

    metadata = PolicyMetadata(
        policy_id="reference-autopilot",
        policy_version="1.0.0",
        adapter_type="deterministic_local",
    )

    def propose(self, observation: SimulationObservation) -> ControlIntent:
        return ControlIntent(
            command_id=f"policy-reference-{observation.time_s:.9f}",
            driver_id=self.metadata.policy_id,
            driver_kind=DriverKind.AUTOPILOT,
            issued_at_s=observation.time_s,
            valid_for_s=1.0,
            mode=IntentMode.AUTOPILOT,
        )


class PolicyDriver:
    """Evaluate policy intent through the authoritative command arbiter."""

    def __init__(self, config: SimulationConfig, policy: PolicyAdapter) -> None:
        self.policy = policy
        self.arbiter = CommandArbiter(config, policy.metadata.policy_id)

    @property
    def metadata(self) -> PolicyMetadata:
        return self.policy.metadata

    def __call__(
        self, observation: SimulationObservation, autopilot_velocity_m_s: np.ndarray
    ) -> CommandDecision:
        return self.arbiter.decide(
            observation, autopilot_velocity_m_s, self.policy.propose(observation)
        )
