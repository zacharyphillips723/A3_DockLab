import time
from pathlib import Path

import numpy as np
import pandas as pd

from a3docklab.config import load_config
from a3docklab.simulation.commands import (
    ControlIntent,
    DriverKind,
    IntentMode,
    SimulationObservation,
)
from a3docklab.simulation.engine import SimulationSession, run_controlled
from a3docklab.simulation.policies import (
    CorridorMpcPolicy,
    FallbackMode,
    MlflowPyfuncPolicy,
    PolicyDriver,
    PolicyHealth,
    PolicyMetadata,
    PolicyRuntimeConfig,
    ReferenceAutopilotPolicy,
    RuleBasedMissionAgent,
    StationKeepingPolicy,
)


def scenario():
    path = Path(__file__).resolve().parents[1] / "configs/scenarios/blue_moon_side.yaml"
    return load_config(path)


def observation() -> SimulationObservation:
    return SimulationObservation(
        run_id="run",
        time_s=2.0,
        phase="far_field_approach",
        position_m=(100.0, 1.0, 0.0),
        velocity_m_s=(-0.1, 0.0, 0.0),
        fuel_mass_kg=1000.0,
        closing_rate_m_s=0.1,
        closing_rate_limit_m_s=0.8,
        corridor_margin_m=1.0,
        keep_out_margin_m=90.0,
        capture_eligible=False,
    )


def test_reference_policy_uses_versioned_guarded_contract() -> None:
    session = SimulationSession(scenario(), active_policy=ReferenceAutopilotPolicy())
    frame = session.step()

    assert frame.decision is not None
    assert frame.decision.driver_id == "reference-autopilot"
    assert frame.decision.requested_mode == "autopilot"
    assert frame.decision.status == "accepted"
    assert frame.state["command_driver_id"] == "reference-autopilot"


def test_reference_active_policy_preserves_reference_physics() -> None:
    config = scenario()
    expected = run_controlled(config).telemetry
    actual = SimulationSession(config, active_policy=ReferenceAutopilotPolicy()).run_to_completion()
    physics_columns = [
        column
        for column in expected.columns
        if not column.startswith(("command_", "policy_", "shadow_policy_"))
    ]

    pd.testing.assert_frame_equal(expected[physics_columns], actual.telemetry[physics_columns])


def test_shadow_policy_is_observable_and_has_no_control_authority() -> None:
    config = scenario()
    expected = run_controlled(config)
    session = SimulationSession(config, shadow_policy=ReferenceAutopilotPolicy())
    first = session.step()
    actual = session.run_to_completion()

    assert first.shadow_policy is not None
    assert first.shadow_policy.policy_id == "reference-autopilot"
    assert first.shadow_decision is not None
    assert first.shadow_decision.status == "accepted"
    physical_columns = [
        column for column in expected.telemetry.columns if not column.startswith("shadow_policy_")
    ]
    pd.testing.assert_frame_equal(
        expected.telemetry[physical_columns], actual.telemetry[physical_columns]
    )
    pd.testing.assert_frame_equal(expected.events, actual.events)


class SlowPolicy:
    metadata = PolicyMetadata(
        policy_id="slow", policy_version="1", adapter_type="test", code_revision="test"
    )

    def propose(self, state: SimulationObservation) -> ControlIntent:
        time.sleep(0.05)
        return StationKeepingPolicy().propose(state)


class FailingPolicy:
    metadata = PolicyMetadata(
        policy_id="failing", policy_version="1", adapter_type="test", code_revision="test"
    )

    def propose(self, state: SimulationObservation) -> ControlIntent:
        raise RuntimeError("model unavailable")


class InvalidPolicy:
    metadata = PolicyMetadata(
        policy_id="invalid", policy_version="1", adapter_type="test", code_revision="test"
    )

    def propose(self, state: SimulationObservation) -> ControlIntent:
        return "not-an-intent"  # type: ignore[return-value]


def test_policy_timeout_and_error_fail_closed() -> None:
    config = scenario()
    velocity = np.asarray([0.8, 0.0, 0.0])
    timeout = PolicyDriver(
        config,
        SlowPolicy(),
        PolicyRuntimeConfig(latency_budget_ms=1, fallback_mode=FallbackMode.HOLD),
    ).evaluate(observation(), velocity)
    failed = PolicyDriver(
        config,
        FailingPolicy(),
        PolicyRuntimeConfig(latency_budget_ms=20, fallback_mode=FallbackMode.AUTOPILOT),
    ).evaluate(observation(), velocity)

    assert timeout.health == PolicyHealth.TIMEOUT
    assert timeout.fallback_applied
    assert timeout.decision.requested_mode == IntentMode.HOLD
    assert timeout.decision.executed_velocity_m_s == (0.0, 0.0, 0.0)
    assert failed.health == PolicyHealth.ERROR
    assert failed.decision.requested_mode == IntentMode.AUTOPILOT
    invalid = PolicyDriver(config, InvalidPolicy()).evaluate(observation(), velocity)
    assert invalid.health == PolicyHealth.INVALID_OUTPUT
    assert invalid.fallback_applied


def test_active_policy_fallback_is_auditable_in_frame_and_telemetry() -> None:
    session = SimulationSession(
        scenario(),
        active_policy=FailingPolicy(),
        active_policy_runtime=PolicyRuntimeConfig(
            latency_budget_ms=20, fallback_mode=FallbackMode.HOLD
        ),
    )
    frame = session.step()

    assert frame.active_policy_evaluation is not None
    assert frame.active_policy_evaluation.health == PolicyHealth.ERROR
    assert frame.state["policy_health"] == "error"
    assert frame.state["policy_fallback_applied"] is True
    assert any(event["event_type"] == "policy_fallback" for event in frame.events)


def test_mpc_and_mission_agent_are_deterministic_and_separate() -> None:
    state = observation()
    mpc = CorridorMpcPolicy()
    assert mpc.propose(state) == mpc.propose(state)
    assert mpc.propose(state).mode == IntentMode.VELOCITY
    assert RuleBasedMissionAgent().decide(state) == "approach"


class FakePyfunc:
    def predict(self, frame: pd.DataFrame) -> pd.DataFrame:
        assert frame.iloc[0]["run_id"] == "run"
        return pd.DataFrame([{"mode": "velocity", "desired_velocity_m_s": [-0.2, 0.0, 0.0]}])


def test_mlflow_pyfunc_adapter_records_registry_provenance() -> None:
    policy = MlflowPyfuncPolicy(
        FakePyfunc(),
        model_uri="models:/dock-policy/7",
        model_version="7",
        code_revision="abc123",
    )
    intent = policy.propose(observation())

    assert intent.driver_kind == DriverKind.MODEL
    assert intent.desired_velocity_m_s == (-0.2, 0.0, 0.0)
    assert policy.metadata.artifact_uri == "models:/dock-policy/7"
    assert policy.metadata.code_revision == "abc123"
