"""In-process control plane for interactive deterministic simulation sessions."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from copy import deepcopy
from secrets import token_urlsafe
from threading import RLock
from typing import Any, cast
from uuid import uuid4

from a3docklab.config import SimulationConfig
from a3docklab.simulation.commands import ControlIntent, DriverKind, IntentMode
from a3docklab.simulation.engine import SimulationFrame, SimulationSession


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
    ) -> None:
        self.scenarios = dict(scenarios)
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._token_factory = token_factory or (lambda: token_urlsafe(32))
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def list_scenarios(self) -> list[dict[str, str]]:
        return [{"id": key, "name": config.name} for key, config in self.scenarios.items()]

    def create(self, scenario_id: str, owner: str, fault: str = "none") -> dict[str, Any]:
        if scenario_id not in self.scenarios:
            raise SessionNotFound(f"unknown scenario: {scenario_id}")
        if fault not in self.FAULTS:
            raise ValueError(f"unsupported fault: {fault}")
        session_id = self._id_factory()
        token = self._token_factory()
        driver_id = f"human:{owner}:{session_id}"
        config = deepcopy(self.scenarios[scenario_id])
        config.handoff.injected_fault = fault  # type: ignore[assignment]
        session = SimulationSession(config, authorized_driver_id=driver_id)
        with self._lock:
            self._sessions[session_id] = {
                "session": session,
                "scenario_id": scenario_id,
                "owner": owner,
                "token": token,
                "driver_id": driver_id,
                "terminated": False,
                "command_sequence": 0,
                "requested_intent": None,
                "last_frame": None,
                "fault": fault,
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
                "lifecycle": lifecycle,
                "step_index": session.step_index,
                "frame": self._frame_payload(entry["last_frame"] or session.current),
                "checkpoint": checkpoint,
                "requested_intent": entry["requested_intent"],
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
                "commands": [
                    intent.model_dump(mode="json") if intent is not None else None
                    for intent in intents
                ],
            }

    def control(
        self,
        session_id: str,
        token: str | None,
        action: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = payload or {}
        with self._lock:
            entry = self._entry(session_id)
            self._authorize(entry, token)
            session: SimulationSession = entry["session"]
            if entry["terminated"] and action != "reset":
                raise SessionConflict("simulation session is terminated")
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
            elif action == "terminate":
                session.pause()
                entry["terminated"] = True
            elif action in {"step", "advance"}:
                if action == "advance" and session.paused:
                    raise SessionConflict("resume the session before advancing")
                intent = self._intent(entry, session, payload.get("intent"))
                try:
                    frame = session.step(intent)
                    entry["last_frame"] = frame
                except StopIteration:
                    frame = entry["last_frame"] or session.current
            else:
                raise ValueError(f"unsupported action: {action}")
            result = self.status(session_id)
            if frame is not None:
                result["frame"] = self._frame_payload(frame)
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
