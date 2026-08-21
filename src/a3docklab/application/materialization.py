"""Portable completed-session materialization contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import pandas as pd
from pydantic import BaseModel

from a3docklab.simulation.engine import SimulationSession


class InteractiveSessionManifest(BaseModel):
    schema_version: str = "1.0"
    session_id: str
    run_id: str
    scenario_id: str
    owner: str
    lifecycle: str
    completed_at_utc: datetime
    telemetry_rows: int
    event_rows: int
    decision_rows: int
    command_rows: int
    policy_evaluation_rows: int
    active_policy_id: str | None = None
    shadow_policy_id: str | None = None
    model_uri: str | None = None
    model_version: str = "unknown"
    code_revision: str = "unknown"
    policy_runtime_json: str = "{}"


@dataclass(frozen=True)
class CompletedSessionArtifact:
    """Immutable logical output handed to local or Lakehouse materializers."""

    manifest: InteractiveSessionManifest
    telemetry: pd.DataFrame
    events: pd.DataFrame
    decisions: pd.DataFrame
    commands: pd.DataFrame
    policy_evaluations: pd.DataFrame


class SessionMaterializer(Protocol):
    def materialize(self, artifact: CompletedSessionArtifact) -> InteractiveSessionManifest: ...


class SessionMaterializationLauncher(Protocol):
    def launch(self, session_id: str, artifact_root: str) -> None: ...


class LocalSessionMaterializer:
    """Write the normalized contract as portable CSV/JSON development artifacts."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def materialize(self, artifact: CompletedSessionArtifact) -> InteractiveSessionManifest:
        destination = self.root / artifact.manifest.session_id
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "manifest.json").write_text(
            artifact.manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        for name in (
            "telemetry",
            "events",
            "decisions",
            "commands",
            "policy_evaluations",
        ):
            getattr(artifact, name).to_csv(destination / f"{name}.csv", index=False)
        return artifact.manifest


class QueuedSessionMaterializer:
    """Stage a portable artifact and enqueue its asynchronous publication job."""

    def __init__(self, root: str | Path, launcher: SessionMaterializationLauncher) -> None:
        self.local = LocalSessionMaterializer(root)
        self.root = str(root)
        self.launcher = launcher

    def materialize(self, artifact: CompletedSessionArtifact) -> InteractiveSessionManifest:
        manifest = self.local.materialize(artifact)
        self.launcher.launch(manifest.session_id, self.root)
        return manifest


def build_completed_session_artifact(
    session: SimulationSession,
    *,
    session_id: str,
    scenario_id: str,
    owner: str,
    lifecycle: Literal["complete", "terminated"],
    completed_at_utc: datetime,
    policy_evaluations: Sequence[Mapping[str, Any]] = (),
    active_policy_id: str | None = None,
    shadow_policy_id: str | None = None,
    model_uri: str | None = None,
    model_version: str = "unknown",
    code_revision: str = "unknown",
    policy_runtime: Mapping[str, Any] | None = None,
) -> CompletedSessionArtifact:
    """Normalize an engine result and its audit history for durable publication."""
    if session.current is None:
        raise ValueError("a session must contain at least one frame before materialization")
    result = session.result()
    telemetry = result.telemetry.copy()
    events = result.events.copy() if result.events is not None else pd.DataFrame()
    decision_columns = [
        column
        for column in telemetry.columns
        if column == "time_s" or column.startswith("command_")
    ]
    decisions = telemetry[decision_columns].copy()
    commands = pd.DataFrame(
        [
            {
                "step_index": index,
                "command_json": (
                    intent.model_dump_json() if intent is not None else json.dumps(None)
                ),
            }
            for index, intent in enumerate(session.checkpoint().intents)
        ]
    )
    evaluations = pd.DataFrame(
        [
            {
                "step_index": record.get("step_index"),
                "authority": record.get("authority"),
                "evaluation_json": json.dumps(record, sort_keys=True),
            }
            for record in policy_evaluations
        ]
    )
    manifest = InteractiveSessionManifest(
        session_id=session_id,
        run_id=session.run_id,
        scenario_id=scenario_id,
        owner=owner,
        lifecycle=lifecycle,
        completed_at_utc=completed_at_utc,
        telemetry_rows=len(telemetry),
        event_rows=len(events),
        decision_rows=len(decisions),
        command_rows=len(commands),
        policy_evaluation_rows=len(evaluations),
        active_policy_id=active_policy_id,
        shadow_policy_id=shadow_policy_id,
        model_uri=model_uri,
        model_version=model_version,
        code_revision=code_revision,
        policy_runtime_json=json.dumps(policy_runtime or {}, sort_keys=True),
    )
    return CompletedSessionArtifact(
        manifest=manifest,
        telemetry=telemetry,
        events=events,
        decisions=decisions,
        commands=commands,
        policy_evaluations=evaluations,
    )
