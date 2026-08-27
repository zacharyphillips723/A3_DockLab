"""In-process control plane for interactive deterministic simulation sessions."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from threading import RLock
from typing import Any, cast
from uuid import uuid4

from a3docklab.application.materialization import (
    InteractiveSessionManifest,
    SessionMaterializer,
    build_completed_session_artifact,
)
from a3docklab.application.state import (
    AcceptedCommand,
    ApplicationStateStore,
    DurableSession,
    hash_lease_token,
)
from a3docklab.config import SimulationConfig
from a3docklab.simulation.commands import ControlIntent, DriverKind, IntentMode
from a3docklab.simulation.engine import (
    SimulationFrame,
    SimulationSession,
    deserialize_checkpoint,
    serialize_checkpoint,
)
from a3docklab.simulation.policies import (
    CorridorMpcPolicy,
    FallbackMode,
    MissionAgentPolicy,
    MlflowPyfuncPolicy,
    PolicyAdapter,
    PolicyRuntimeConfig,
    ReferenceAutopilotPolicy,
    StationKeepingPolicy,
)


class SessionError(RuntimeError):
    """Base class for live-session request failures."""


class SessionNotFound(SessionError):
    pass


class SessionConflict(SessionError):
    pass


class SessionUnauthorized(SessionError):
    pass


class InteractiveSimulationService:
    """Thread-safe registry and exclusive control lease for live simulations."""

    FAULTS = (
        "none",
        "stale_data",
        "frame_mismatch",
        "actuator_unhealthy",
        "lost_acknowledgement",
        "duplicated_authority",
        "lost_authority",
        "shadow_command_mismatch",
        "active_owner_failure",
    )

    def __init__(
        self,
        scenarios: Mapping[str, SimulationConfig],
        *,
        id_factory: Callable[[], str] | None = None,
        token_factory: Callable[[], str] | None = None,
        state_store: ApplicationStateStore | None = None,
        lease_duration: timedelta = timedelta(seconds=30),
        clock: Callable[[], datetime] | None = None,
        holder_id: str | None = None,
        materializer: SessionMaterializer | None = None,
        max_active_sessions: int = 32,
        max_active_sessions_per_owner: int = 4,
    ) -> None:
        self.scenarios = dict(scenarios)
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._token_factory = token_factory or (lambda: token_urlsafe(32))
        self._state_store = state_store
        self._lease_duration = lease_duration
        self._clock = clock or (lambda: datetime.now(UTC))
        self._holder_id = holder_id or f"app:{uuid4()}"
        self._materializer = materializer
        if max_active_sessions < 1 or max_active_sessions_per_owner < 1:
            raise ValueError("session quotas must be positive")
        self._max_active_sessions = max_active_sessions
        self._max_active_sessions_per_owner = max_active_sessions_per_owner
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def list_scenarios(self) -> list[dict[str, str]]:
        return [{"id": key, "name": config.name} for key, config in self.scenarios.items()]

    @staticmethod
    def list_policies() -> list[dict[str, str]]:
        return [
            {"id": "reference-autopilot", "name": "Reference autopilot"},
            {"id": "station-keeping", "name": "Station keeping"},
            {"id": "corridor-mpc", "name": "Corridor MPC"},
            {"id": "mission-agent", "name": "Rule-based mission agent"},
        ]

    @staticmethod
    def _policy(
        policy_id: str | None,
        *,
        model_uri: str | None = None,
        model_version: str = "unknown",
        code_revision: str = "unknown",
    ) -> PolicyAdapter | None:
        if policy_id is None:
            return None
        policies: dict[str, Callable[[], PolicyAdapter]] = {
            "reference-autopilot": ReferenceAutopilotPolicy,
            "station-keeping": StationKeepingPolicy,
            "corridor-mpc": CorridorMpcPolicy,
            "mission-agent": MissionAgentPolicy,
        }
        if policy_id == "mlflow":
            if not model_uri:
                raise ValueError("model_uri is required for an MLflow policy")
            return MlflowPyfuncPolicy.load(model_uri, model_version, code_revision)
        try:
            return policies[policy_id]()
        except KeyError as exc:
            raise ValueError(f"unknown policy: {policy_id}") from exc

    def create(
        self,
        scenario_id: str,
        owner: str,
        fault: str = "none",
        active_policy_id: str | None = None,
        shadow_policy_id: str | None = None,
        latency_budget_ms: float = 50.0,
        fallback_mode: str = "hold",
        model_uri: str | None = None,
        model_version: str = "unknown",
        code_revision: str = "unknown",
    ) -> dict[str, Any]:
        if scenario_id not in self.scenarios:
            raise SessionNotFound(f"unknown scenario: {scenario_id}")
        if fault not in self.FAULTS:
            raise ValueError(f"unsupported fault: {fault}")
        with self._lock:
            active = [
                entry
                for entry in self._sessions.values()
                if not entry["terminated"] and not entry["session"].complete
            ]
            if len(active) >= self._max_active_sessions:
                raise SessionConflict("active session quota exceeded")
            if sum(entry["owner"] == owner for entry in active) >= self._max_active_sessions_per_owner:
                raise SessionConflict("active session quota exceeded for owner")
        session_id = self._id_factory()
        token = self._token_factory()
        driver_id = f"human:{owner}:{session_id}"
        config = deepcopy(self.scenarios[scenario_id])
        config.handoff.injected_fault = fault  # type: ignore[assignment]
        active_policy = self._policy(
            active_policy_id,
            model_uri=model_uri,
            model_version=model_version,
            code_revision=code_revision,
        )
        shadow_policy = self._policy(
            shadow_policy_id,
            model_uri=model_uri,
            model_version=model_version,
            code_revision=code_revision,
        )
        runtime = PolicyRuntimeConfig(
            latency_budget_ms=latency_budget_ms,
            fallback_mode=FallbackMode(fallback_mode),
        )
        session = SimulationSession(
            config,
            authorized_driver_id=driver_id,
            active_policy=active_policy,
            shadow_policy=shadow_policy,
            active_policy_runtime=runtime,
            shadow_policy_runtime=runtime,
        )
        persisted_version: int | None = None
        now = self._clock()
        if self._state_store is not None:
            self._state_store.create_durable_session(
                DurableSession(
                    session_id=session_id,
                    scenario_id=scenario_id,
                    owner=owner,
                    status="paused",
                    checkpoint={
                        "fault": fault,
                        "active_policy_id": active_policy_id,
                        "shadow_policy_id": shadow_policy_id,
                        "policy_runtime": runtime.model_dump(mode="json"),
                    },
                    updated_at_utc=now,
                )
            )
            persisted = self._state_store.acquire_session_lease(
                session_id,
                self._holder_id,
                hash_lease_token(token),
                now + self._lease_duration,
                expected_version=0,
                now_utc=now,
            )
            persisted_version = persisted.version
        with self._lock:
            self._sessions[session_id] = {
                "session": session,
                "scenario_id": scenario_id,
                "owner": owner,
                "token": token,
                "driver_id": driver_id,
                "terminated": False,
                "command_sequence": 0,
                "request_sequence": 0,
                "requested_intent": None,
                "last_frame": None,
                "fault": fault,
                "active_policy_id": active_policy_id,
                "shadow_policy_id": shadow_policy_id,
                "policy_runtime": runtime.model_dump(mode="json"),
                "policy_evaluations": [],
                "persisted_version": persisted_version,
                "model_uri": model_uri,
                "model_version": model_version,
                "code_revision": code_revision,
                "materialized_manifest": None,
                "created_at_utc": now,
                "frame_count": 0,
                "safety_interventions": 0,
            }
        status = self.status(session_id)
        status["control_token"] = token
        return status

    def restore(self, session_id: str, owner: str) -> dict[str, Any]:
        """Take over an expired durable lease and rebuild its deterministic engine."""
        if self._state_store is None:
            raise SessionConflict("durable session recovery is unavailable")
        durable = self._state_store.get_durable_session(session_id)
        if durable is None:
            raise SessionNotFound("simulation session not found")
        if durable.owner != owner:
            raise SessionUnauthorized("only the session owner can restore it")
        metadata = durable.checkpoint or {}
        engine_payload = metadata.get("engine_checkpoint")
        if not isinstance(engine_payload, dict):
            raise SessionConflict("the session does not have a restorable engine checkpoint")
        if durable.scenario_id not in self.scenarios:
            raise SessionNotFound(f"unknown scenario: {durable.scenario_id}")

        token = self._token_factory()
        now = self._clock()
        try:
            leased = self._state_store.acquire_session_lease(
                session_id,
                self._holder_id,
                hash_lease_token(token),
                now + self._lease_duration,
                durable.version,
                now,
            )
        except RuntimeError as exc:
            raise SessionConflict("the existing control lease has not expired") from exc

        fault = str(metadata.get("fault", "none"))
        active_policy_id = metadata.get("active_policy_id")
        shadow_policy_id = metadata.get("shadow_policy_id")
        model_uri = metadata.get("model_uri")
        model_version = str(metadata.get("model_version", "unknown"))
        code_revision = str(metadata.get("code_revision", "unknown"))
        runtime = PolicyRuntimeConfig.model_validate(metadata.get("policy_runtime", {}))
        config = deepcopy(self.scenarios[durable.scenario_id])
        config.handoff.injected_fault = fault  # type: ignore[assignment]
        driver_id = f"human:{owner}:{session_id}"
        session = SimulationSession(
            config,
            authorized_driver_id=driver_id,
            active_policy=self._policy(
                active_policy_id,
                model_uri=model_uri,
                model_version=model_version,
                code_revision=code_revision,
            ),
            shadow_policy=self._policy(
                shadow_policy_id,
                model_uri=model_uri,
                model_version=model_version,
                code_revision=code_revision,
            ),
            active_policy_runtime=runtime,
            shadow_policy_runtime=runtime,
        )
        restored_frame = session.restore(deserialize_checkpoint(engine_payload))
        if durable.status == "running":
            session.resume()
        checkpoint = session.checkpoint()
        with self._lock:
            self._sessions[session_id] = {
                "session": session,
                "scenario_id": durable.scenario_id,
                "owner": owner,
                "token": token,
                "driver_id": driver_id,
                "terminated": durable.status == "terminated",
                "command_sequence": sum(intent is not None for intent in checkpoint.intents),
                "request_sequence": 0,
                "requested_intent": metadata.get("requested_intent"),
                "last_frame": restored_frame,
                "fault": fault,
                "active_policy_id": active_policy_id,
                "shadow_policy_id": shadow_policy_id,
                "policy_runtime": runtime.model_dump(mode="json"),
                "policy_evaluations": [],
                "persisted_version": leased.version,
                "model_uri": model_uri,
                "model_version": model_version,
                "code_revision": code_revision,
                "materialized_manifest": metadata.get("materialized_manifest"),
                "created_at_utc": durable.updated_at_utc,
                "frame_count": int(metadata.get("frame_count", checkpoint.step_index + 1)),
                "safety_interventions": int(metadata.get("safety_interventions", 0)),
            }
        status = self.status(session_id)
        status["control_token"] = token
        return status

    def _entry(self, session_id: str) -> dict[str, Any]:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFound("simulation session not found") from exc

    @staticmethod
    def _authorize(entry: dict[str, Any], token: str | None) -> None:
        if not token or token != entry["token"]:
            raise SessionUnauthorized("the active control lease is required")

    def _renew_durable_lease(self, session_id: str, entry: dict[str, Any], token: str) -> None:
        if self._state_store is None:
            return
        expected_version = cast(int, entry["persisted_version"])
        now = self._clock()
        try:
            durable = self._state_store.renew_session_lease(
                session_id,
                self._holder_id,
                hash_lease_token(token),
                now + self._lease_duration,
                expected_version,
                now,
            )
        except RuntimeError as exc:
            raise SessionUnauthorized("the durable control lease is expired or stale") from exc
        entry["persisted_version"] = durable.version

    def _persist_checkpoint(self, session_id: str, entry: dict[str, Any]) -> None:
        if self._state_store is None:
            return
        snapshot = self.status(session_id)
        session: SimulationSession = entry["session"]
        engine_checkpoint = (
            serialize_checkpoint(session.checkpoint()) if session.current is not None else None
        )
        try:
            durable = self._state_store.update_session_checkpoint(
                session_id,
                snapshot["lifecycle"],
                {
                    "fault": entry["fault"],
                    "active_policy_id": entry["active_policy_id"],
                    "shadow_policy_id": entry["shadow_policy_id"],
                    "policy_runtime": entry["policy_runtime"],
                    "model_uri": entry["model_uri"],
                    "model_version": entry["model_version"],
                    "code_revision": entry["code_revision"],
                    "materialized_manifest": entry["materialized_manifest"],
                    "step_index": snapshot["step_index"],
                    "checkpoint": snapshot["checkpoint"],
                    "engine_checkpoint": engine_checkpoint,
                    "requested_intent": snapshot["requested_intent"],
                    "frame_count": entry["frame_count"],
                    "safety_interventions": entry["safety_interventions"],
                },
                cast(int, entry["persisted_version"]),
            )
        except RuntimeError as exc:
            raise SessionConflict("a newer session checkpoint already exists") from exc
        entry["persisted_version"] = durable.version

    def _accept_durable_command(
        self,
        session_id: str,
        entry: dict[str, Any],
        action: str,
        payload: Mapping[str, Any],
        actor: str,
        idempotency_key: str,
    ) -> bool:
        if self._state_store is None:
            return True
        command = AcceptedCommand(
            session_id=session_id,
            command_id=f"{session_id}:request:{uuid4()}",
            idempotency_key=idempotency_key,
            actor=actor,
            payload={"action": action, "payload": self._json_safe(dict(payload))},
            accepted_at_utc=self._clock(),
        )
        try:
            saved = self._state_store.record_accepted_command(command)
        except RuntimeError as exc:
            raise SessionConflict(str(exc)) from exc
        return saved.command_id == command.command_id

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: InteractiveSimulationService._json_safe(item) for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [InteractiveSimulationService._json_safe(item) for item in value]
        if hasattr(value, "item"):
            return InteractiveSimulationService._json_safe(value.item())
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    @staticmethod
    def _frame_payload(frame: SimulationFrame | None) -> dict[str, Any] | None:
        if frame is None:
            return None
        return {
            "state": InteractiveSimulationService._json_safe(frame.state),
            "events": InteractiveSimulationService._json_safe(frame.events),
            "decision": frame.decision.model_dump(mode="json") if frame.decision else None,
            "shadow_decision": (
                frame.shadow_decision.model_dump(mode="json") if frame.shadow_decision else None
            ),
            "shadow_policy": (
                frame.shadow_policy.model_dump(mode="json") if frame.shadow_policy else None
            ),
            "active_policy_evaluation": (
                frame.active_policy_evaluation.model_dump(mode="json")
                if frame.active_policy_evaluation
                else None
            ),
            "shadow_policy_evaluation": (
                frame.shadow_policy_evaluation.model_dump(mode="json")
                if frame.shadow_policy_evaluation
                else None
            ),
        }

    def status(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._entry(session_id)
            session: SimulationSession = entry["session"]
            lifecycle = (
                "terminated"
                if entry["terminated"]
                else "complete"
                if session.complete
                else "paused"
                if session.paused
                else "running"
            )
            checkpoint = None
            if session.current is not None:
                saved = session.checkpoint()
                checkpoint = {
                    "run_id": saved.run_id,
                    "step_index": saved.step_index,
                    "time_s": saved.time_s,
                    "state": self._json_safe(saved.state),
                    "command_count": len(saved.intents),
                }
            return {
                "session_id": session_id,
                "scenario_id": entry["scenario_id"],
                "owner": entry["owner"],
                "fault": entry["fault"],
                "active_policy_id": entry["active_policy_id"],
                "shadow_policy_id": entry["shadow_policy_id"],
                "policy_runtime": entry["policy_runtime"],
                "lifecycle": lifecycle,
                "step_index": session.step_index,
                "frame": self._frame_payload(entry["last_frame"] or session.current),
                "checkpoint": checkpoint,
                "requested_intent": entry["requested_intent"],
            }

    def operational_snapshot(self) -> dict[str, Any]:
        """Return low-cardinality service metrics without exposing session payloads."""
        with self._lock:
            entries = list(self._sessions.values())
            active = [
                entry
                for entry in entries
                if not entry["terminated"] and not entry["session"].complete
            ]
            evaluations = [
                evaluation
                for entry in entries
                for evaluation in entry["policy_evaluations"]
            ]
            latencies = sorted(float(item.get("latency_ms", 0.0)) for item in evaluations)
            p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1)
            simulation_steps = sum(int(entry["frame_count"]) for entry in entries)
            simulated_seconds = sum(
                float(entry["session"].current.state.get("time_s", 0.0))
                for entry in entries
                if entry["session"].current is not None
            )
            safety_interventions = sum(int(entry["safety_interventions"]) for entry in entries)
            oldest = min((entry["created_at_utc"] for entry in entries), default=self._clock())
            elapsed_s = max((self._clock() - oldest).total_seconds(), 1e-9)
            return {
                "total_sessions": len(entries),
                "active_sessions": len(active),
                "simulation_steps": simulation_steps,
                "simulation_rate_hz": simulation_steps / elapsed_s,
                "simulated_seconds": simulated_seconds,
                "policy_evaluations": len(evaluations),
                "policy_latency_p95_ms": latencies[p95_index] if latencies else 0.0,
                "policy_budget_exceeded": sum(
                    float(item.get("latency_ms", 0.0))
                    > float(item.get("latency_budget_ms", float("inf")))
                    for item in evaluations
                ),
                "safety_interventions": safety_interventions,
                "session_quota": self._max_active_sessions,
                "owner_session_quota": self._max_active_sessions_per_owner,
            }

    def command_log(self, session_id: str) -> dict[str, Any]:
        """Export the deterministic intent sequence needed to reproduce a run."""
        with self._lock:
            entry = self._entry(session_id)
            session: SimulationSession = entry["session"]
            intents = session.checkpoint().intents if session.current is not None else ()
            return {
                "schema_version": "1.0",
                "session_id": session_id,
                "run_id": session.run_id,
                "scenario_id": entry["scenario_id"],
                "fault": entry["fault"],
                "active_policy_id": entry["active_policy_id"],
                "shadow_policy_id": entry["shadow_policy_id"],
                "policy_runtime": entry["policy_runtime"],
                "commands": [
                    intent.model_dump(mode="json") if intent is not None else None
                    for intent in intents
                ],
            }

    def policy_evaluations(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            entry = self._entry(session_id)
            return {
                "schema_version": "1.0",
                "session_id": session_id,
                "evaluations": deepcopy(entry["policy_evaluations"]),
            }

    def materialize(self, session_id: str) -> InteractiveSessionManifest:
        """Publish one terminal session through the configured platform boundary."""
        if self._materializer is None:
            raise SessionConflict("session materialization is unavailable")
        with self._lock:
            entry = self._entry(session_id)
            existing = entry["materialized_manifest"]
            if existing is not None:
                return InteractiveSessionManifest.model_validate(existing)
            snapshot = self.status(session_id)
            lifecycle = snapshot["lifecycle"]
            if lifecycle not in {"complete", "terminated"}:
                raise SessionConflict("only terminal sessions can be materialized")
            artifact = build_completed_session_artifact(
                entry["session"],
                session_id=session_id,
                scenario_id=entry["scenario_id"],
                owner=entry["owner"],
                lifecycle=lifecycle,
                completed_at_utc=self._clock(),
                policy_evaluations=entry["policy_evaluations"],
                active_policy_id=entry["active_policy_id"],
                shadow_policy_id=entry["shadow_policy_id"],
                model_uri=entry["model_uri"],
                model_version=entry["model_version"],
                code_revision=entry["code_revision"],
                policy_runtime=entry["policy_runtime"],
            )
            manifest = self._materializer.materialize(artifact)
            entry["materialized_manifest"] = manifest.model_dump(mode="json")
            self._persist_checkpoint(session_id, entry)
            return manifest

    def control(
        self,
        session_id: str,
        token: str | None,
        action: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload = payload or {}
        with self._lock:
            entry = self._entry(session_id)
            self._authorize(entry, token)
            session: SimulationSession = entry["session"]
            allowed_actions = {"pause", "resume", "reset", "terminate", "step", "advance"}
            if action not in allowed_actions:
                raise ValueError(f"unsupported action: {action}")
            if entry["terminated"] and action != "reset":
                raise SessionConflict("simulation session is terminated")
            if action == "advance" and session.paused:
                raise SessionConflict("resume the session before advancing")
            if self._state_store is not None:
                if not actor or actor != entry["owner"]:
                    raise SessionUnauthorized("the session owner identity is required")
                if not idempotency_key:
                    raise ValueError("Idempotency-Key is required for durable sessions")
                assert token is not None
                self._renew_durable_lease(session_id, entry, token)
                if not self._accept_durable_command(
                    session_id, entry, action, payload, actor, idempotency_key
                ):
                    return self.status(session_id)
            frame: SimulationFrame | None = None
            if action == "pause":
                session.pause()
            elif action == "resume":
                session.resume()
            elif action == "reset":
                session.reset()
                entry["terminated"] = False
                entry["command_sequence"] = 0
                entry["requested_intent"] = None
                entry["last_frame"] = None
                entry["policy_evaluations"] = []
                entry["materialized_manifest"] = None
                entry["frame_count"] = 0
                entry["safety_interventions"] = 0
            elif action == "terminate":
                session.pause()
                entry["terminated"] = True
            elif action in {"step", "advance"}:
                intent = self._intent(entry, session, payload.get("intent"))
                try:
                    frame = session.step(intent)
                    entry["last_frame"] = frame
                    entry["frame_count"] += 1
                    if frame.decision is not None and frame.decision.status.value != "accepted":
                        entry["safety_interventions"] += 1
                    for authority, evaluation in (
                        ("active", frame.active_policy_evaluation),
                        ("shadow", frame.shadow_policy_evaluation),
                    ):
                        if evaluation is not None:
                            record = evaluation.model_dump(mode="json")
                            record["authority"] = authority
                            record["step_index"] = session.step_index
                            entry["policy_evaluations"].append(record)
                except StopIteration:
                    frame = entry["last_frame"] or session.current
            result = self.status(session_id)
            if frame is not None:
                result["frame"] = self._frame_payload(frame)
            self._persist_checkpoint(session_id, entry)
            if result["lifecycle"] in {"complete", "terminated"} and self._materializer:
                result["materialization"] = self.materialize(session_id).model_dump(mode="json")
            return result

    def _intent(
        self,
        entry: dict[str, Any],
        session: SimulationSession,
        payload: Any,
    ) -> ControlIntent | None:
        if payload is None:
            entry["requested_intent"] = None
            return None
        entry["command_sequence"] += 1
        issued_at = cast(float, session.current.state["time_s"]) if session.current else 0.0
        intent = ControlIntent(
            command_id=f"live-{entry['command_sequence']}",
            driver_id=entry["driver_id"],
            driver_kind=DriverKind.HUMAN,
            issued_at_s=issued_at,
            valid_for_s=float(payload.get("valid_for_s", session.config.step_s * 2)),
            mode=IntentMode(str(payload.get("mode", "autopilot"))),
            desired_velocity_m_s=payload.get("desired_velocity_m_s"),
            desired_torque_n_m=payload.get("desired_torque_n_m"),
        )
        entry["requested_intent"] = intent.model_dump(mode="json")
        return intent
