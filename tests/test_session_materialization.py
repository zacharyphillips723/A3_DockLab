from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from a3docklab.application.materialization import (
    CompletedSessionArtifact,
    InteractiveSessionManifest,
    LocalSessionMaterializer,
    QueuedSessionMaterializer,
    build_completed_session_artifact,
)
from a3docklab.application.sessions import InteractiveSimulationService
from a3docklab.config import load_config
from a3docklab.platform.delta import DeltaSessionMaterializer, InMemoryDeltaCatalog
from a3docklab.simulation.engine import SimulationSession


def _artifact() -> CompletedSessionArtifact:
    frames = {
        "telemetry": pd.DataFrame([{"time_s": 0.0, "range_m": 10.0}]),
        "events": pd.DataFrame([{"time_s": 0.0, "event_type": "created"}]),
        "decisions": pd.DataFrame([{"time_s": 0.0, "status": "accepted"}]),
        "commands": pd.DataFrame([{"step_index": 0, "command_json": '{"mode":"hold"}'}]),
        "policy_evaluations": pd.DataFrame(),
    }
    manifest = InteractiveSessionManifest(
        session_id="session-1",
        run_id="run-1",
        scenario_id="nominal",
        owner="pilot@example.com",
        lifecycle="complete",
        completed_at_utc=datetime(2026, 8, 21, tzinfo=UTC),
        telemetry_rows=len(frames["telemetry"]),
        event_rows=len(frames["events"]),
        decision_rows=len(frames["decisions"]),
        command_rows=len(frames["commands"]),
        policy_evaluation_rows=len(frames["policy_evaluations"]),
    )
    return CompletedSessionArtifact(manifest=manifest, **frames)


def test_local_session_materializer_writes_portable_contract(tmp_path: Path) -> None:
    artifact = _artifact()
    result = LocalSessionMaterializer(tmp_path).materialize(artifact)
    destination = tmp_path / "session-1"

    assert result == artifact.manifest
    assert (destination / "manifest.json").is_file()
    assert pd.read_csv(destination / "telemetry.csv")["range_m"].tolist() == [10.0]
    assert (destination / "policy_evaluations.csv").is_file()


def test_queued_materializer_stages_before_launching_job(tmp_path: Path) -> None:
    launches: list[tuple[str, str]] = []

    class RecordingLauncher:
        def launch(self, session_id: str, artifact_root: str) -> None:
            assert (Path(artifact_root) / session_id / "manifest.json").is_file()
            launches.append((session_id, artifact_root))

    artifact = _artifact()
    result = QueuedSessionMaterializer(tmp_path, RecordingLauncher()).materialize(artifact)

    assert result == artifact.manifest
    assert launches == [("session-1", str(tmp_path))]


def test_delta_session_materializer_appends_normalized_identity_columns() -> None:
    artifact = _artifact()
    catalog = InMemoryDeltaCatalog()
    result = DeltaSessionMaterializer(catalog, "test_a3").materialize(artifact)

    assert result == artifact.manifest
    assert set(catalog.tables) == {
        "test_a3_interactive_sessions",
        "test_a3_interactive_telemetry",
        "test_a3_interactive_events",
        "test_a3_interactive_decisions",
        "test_a3_interactive_commands",
    }
    telemetry = catalog.tables["test_a3_interactive_telemetry"]
    assert telemetry.loc[0, "session_id"] == "session-1"
    assert telemetry.loc[0, "owner"] == "pilot@example.com"
    assert "manifest_json" in catalog.tables["test_a3_interactive_sessions"]


def test_completed_session_builder_normalizes_engine_and_audit_outputs() -> None:
    root = Path(__file__).resolve().parents[1]
    session = SimulationSession(load_config(root / "configs/scenarios/blue_moon_side.yaml"))
    session.step()

    artifact = build_completed_session_artifact(
        session,
        session_id="session-1",
        scenario_id="blue_moon_side",
        owner="pilot@example.com",
        lifecycle="terminated",
        completed_at_utc=datetime(2026, 8, 21, tzinfo=UTC),
        policy_evaluations=[{"step_index": 0, "authority": "shadow", "health": "healthy"}],
    )

    assert artifact.manifest.telemetry_rows == 1
    assert artifact.manifest.command_rows == 1
    assert artifact.manifest.policy_evaluation_rows == 1
    assert "command_requested_mode" in artifact.decisions
    assert artifact.commands.loc[0, "command_json"] == "null"
    assert artifact.policy_evaluations.loc[0, "authority"] == "shadow"


def test_terminal_live_session_materializes_once() -> None:
    root = Path(__file__).resolve().parents[1]
    catalog = InMemoryDeltaCatalog()
    materializer = DeltaSessionMaterializer(catalog, "test_a3")
    service = InteractiveSimulationService(
        {"blue_moon_side": load_config(root / "configs/scenarios/blue_moon_side.yaml")},
        id_factory=lambda: "session-1",
        token_factory=lambda: "lease-1",
        materializer=materializer,
    )
    created = service.create("blue_moon_side", "pilot@example.com")
    service.control("session-1", created["control_token"], "step")
    terminated = service.control("session-1", created["control_token"], "terminate")

    assert terminated["materialization"]["session_id"] == "session-1"
    assert terminated["materialization"]["lifecycle"] == "terminated"
    assert len(catalog.tables["test_a3_interactive_sessions"]) == 1
    assert service.materialize("session-1").session_id == "session-1"
    assert len(catalog.tables["test_a3_interactive_sessions"]) == 1
