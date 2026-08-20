"""Versioned, guarded policy runtimes for active and shadow drivers."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from enum import StrEnum
from typing import Any, Protocol, cast

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


class PolicyHealth(StrEnum):
    HEALTHY = "healthy"
    TIMEOUT = "timeout"
    ERROR = "error"
    INVALID_OUTPUT = "invalid_output"


class FallbackMode(StrEnum):
    HOLD = "hold"
    AUTOPILOT = "autopilot"


class PolicyRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    latency_budget_ms: float = Field(default=50.0, gt=0.0)
    fallback_mode: FallbackMode = FallbackMode.HOLD


class PolicyMetadata(BaseModel):
    """Immutable policy and schema provenance recorded with every evaluation."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    adapter_type: str = Field(min_length=1)
    driver_kind: DriverKind = DriverKind.MODEL
    observation_schema_version: str = "1.0"
    action_schema_version: str = "1.0"
    artifact_uri: str = "local://built-in"
    configuration_digest: str = "built-in"
    code_revision: str = "working-tree"


class PolicyEvaluation(BaseModel):
    """Auditable outcome of one guarded policy call."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0"
    policy: PolicyMetadata
    observation_time_s: float
    latency_ms: float = Field(ge=0.0)
    latency_budget_ms: float = Field(gt=0.0)
    health: PolicyHealth
    detail: str
    fallback_applied: bool = False
    requested_intent: ControlIntent | None = None
    decision: CommandDecision


class PolicyAdapter(Protocol):
    @property
    def metadata(self) -> PolicyMetadata: ...

    def propose(self, observation: SimulationObservation) -> ControlIntent: ...


def configuration_digest(values: Mapping[str, object]) -> str:
    payload = json.dumps(dict(values), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _intent(
    metadata: PolicyMetadata,
    observation: SimulationObservation,
    mode: IntentMode,
    velocity: tuple[float, float, float] | None = None,
) -> ControlIntent:
    return ControlIntent(
        command_id=f"{metadata.policy_id}-{observation.time_s:.9f}",
        driver_id=metadata.policy_id,
        driver_kind=metadata.driver_kind,
        issued_at_s=observation.time_s,
        valid_for_s=1.0,
        mode=mode,
        desired_velocity_m_s=velocity,
    )


class ReferenceAutopilotPolicy:
    metadata = PolicyMetadata(
        policy_id="reference-autopilot",
        policy_version="1.0.0",
        adapter_type="deterministic_local",
        driver_kind=DriverKind.AUTOPILOT,
    )

    def propose(self, observation: SimulationObservation) -> ControlIntent:
        return _intent(self.metadata, observation, IntentMode.AUTOPILOT)


class StationKeepingPolicy:
    metadata = PolicyMetadata(
        policy_id="station-keeping",
        policy_version="1.0.0",
        adapter_type="deterministic_local",
        configuration_digest=configuration_digest({"velocity": [0.0, 0.0, 0.0]}),
    )

    def propose(self, observation: SimulationObservation) -> ControlIntent:
        return _intent(self.metadata, observation, IntentMode.HOLD)


class CorridorMpcPolicy:
    """Deterministic receding-horizon approximation for guarded approach testing."""

    def __init__(self, horizon_steps: int = 12, position_gain: float = 0.002) -> None:
        self.horizon_steps = horizon_steps
        self.position_gain = position_gain
        config = {"horizon_steps": horizon_steps, "position_gain": position_gain}
        self.metadata = PolicyMetadata(
            policy_id="corridor-mpc",
            policy_version="1.0.0",
            adapter_type="deterministic_mpc",
            configuration_digest=configuration_digest(config),
        )

    def propose(self, observation: SimulationObservation) -> ControlIntent:
        position = np.asarray(observation.position_m, dtype=np.float64)
        target = -self.position_gain * position
        maximum = max(0.01, observation.closing_rate_limit_m_s)
        norm = float(np.linalg.norm(target))
        if norm > maximum:
            target *= maximum / norm
        velocity = cast(tuple[float, float, float], tuple(float(value) for value in target))
        return _intent(self.metadata, observation, IntentMode.VELOCITY, velocity)


class MissionDirective(StrEnum):
    APPROACH = "approach"
    HOLD = "hold"
    RETREAT = "retreat"
    ABORT = "abort"


class RuleBasedMissionAgent:
    """High-level mission logic deliberately separated from control-rate policy."""

    def decide(self, observation: SimulationObservation) -> MissionDirective:
        if observation.keep_out_margin_m < 0 or observation.corridor_margin_m < 0:
            return MissionDirective.RETREAT
        if observation.closing_rate_m_s > observation.closing_rate_limit_m_s:
            return MissionDirective.HOLD
        return MissionDirective.APPROACH


class MissionAgentPolicy:
    def __init__(self, agent: RuleBasedMissionAgent | None = None) -> None:
        self.agent = agent or RuleBasedMissionAgent()
        self.metadata = PolicyMetadata(
            policy_id="rule-mission-agent",
            policy_version="1.0.0",
            adapter_type="high_level_agent_adapter",
        )

    def propose(self, observation: SimulationObservation) -> ControlIntent:
        directive = self.agent.decide(observation)
        modes = {
            MissionDirective.APPROACH: IntentMode.AUTOPILOT,
            MissionDirective.HOLD: IntentMode.HOLD,
            MissionDirective.RETREAT: IntentMode.RETREAT,
            MissionDirective.ABORT: IntentMode.ABORT,
        }
        return _intent(self.metadata, observation, modes[directive])


class MlflowPyfuncPolicy:
    """Adapter for an already loaded MLflow pyfunc model and its registry provenance."""

    def __init__(
        self,
        model: Any,
        *,
        model_uri: str,
        model_version: str,
        code_revision: str,
    ) -> None:
        self.model = model
        self.metadata = PolicyMetadata(
            policy_id="mlflow-pyfunc",
            policy_version=model_version,
            adapter_type="mlflow_pyfunc",
            artifact_uri=model_uri,
            configuration_digest=configuration_digest({"model_uri": model_uri}),
            code_revision=code_revision,
        )

    @classmethod
    def load(cls, model_uri: str, model_version: str, code_revision: str) -> MlflowPyfuncPolicy:
        try:
            import mlflow.pyfunc  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "MLflow policy loading requires the 'ml' optional dependency"
            ) from exc
        return cls(
            mlflow.pyfunc.load_model(model_uri),
            model_uri=model_uri,
            model_version=model_version,
            code_revision=code_revision,
        )

    def propose(self, observation: SimulationObservation) -> ControlIntent:
        import pandas as pd

        output = self.model.predict(pd.DataFrame([observation.model_dump(mode="json")]))
        row = output.iloc[0] if hasattr(output, "iloc") else output[0]
        values = row.to_dict() if hasattr(row, "to_dict") else dict(row)
        return _intent(
            self.metadata,
            observation,
            IntentMode(str(values["mode"])),
            tuple(values["desired_velocity_m_s"]) if values.get("desired_velocity_m_s") else None,
        )


class PolicyDriver:
    """Execute a policy within a budget and apply deterministic safe fallback."""

    def __init__(
        self,
        config: SimulationConfig,
        policy: PolicyAdapter,
        runtime: PolicyRuntimeConfig | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.policy = policy
        self.runtime = runtime or PolicyRuntimeConfig()
        self.arbiter = CommandArbiter(config, policy.metadata.policy_id)
        self.clock = clock
        self.last_evaluation: PolicyEvaluation | None = None

    @property
    def metadata(self) -> PolicyMetadata:
        return self.policy.metadata

    def evaluate(
        self, observation: SimulationObservation, autopilot_velocity_m_s: np.ndarray
    ) -> PolicyEvaluation:
        started = self.clock()
        requested: ControlIntent | None = None
        health = PolicyHealth.HEALTHY
        detail = "policy_healthy"
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="a3docklab-policy")
        future = executor.submit(self.policy.propose, observation)
        try:
            proposed = future.result(timeout=self.runtime.latency_budget_ms / 1000.0)
            if not isinstance(proposed, ControlIntent):
                raise TypeError("policy output is not a ControlIntent")
            requested = proposed
        except FutureTimeout:
            health = PolicyHealth.TIMEOUT
            detail = "latency_budget_exceeded"
        except (TypeError, ValueError) as exc:
            health = PolicyHealth.INVALID_OUTPUT
            detail = f"invalid_output:{type(exc).__name__}"
        except Exception as exc:  # noqa: BLE001 - external policy trust boundary
            health = PolicyHealth.ERROR
            detail = f"policy_error:{type(exc).__name__}"
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        elapsed_ms = max(0.0, (self.clock() - started) * 1000.0)
        fallback = health != PolicyHealth.HEALTHY
        if fallback:
            mode = IntentMode(self.runtime.fallback_mode.value)
            requested = _intent(self.metadata, observation, mode)
        assert requested is not None
        decision = self.arbiter.decide(observation, autopilot_velocity_m_s, requested)
        evaluation = PolicyEvaluation(
            policy=self.metadata,
            observation_time_s=observation.time_s,
            latency_ms=elapsed_ms,
            latency_budget_ms=self.runtime.latency_budget_ms,
            health=health,
            detail=detail,
            fallback_applied=fallback,
            requested_intent=requested,
            decision=decision,
        )
        self.last_evaluation = evaluation
        return evaluation

    def __call__(
        self, observation: SimulationObservation, autopilot_velocity_m_s: np.ndarray
    ) -> CommandDecision:
        return self.evaluate(observation, autopilot_velocity_m_s).decision
