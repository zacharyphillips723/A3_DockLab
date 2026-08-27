import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from time import perf_counter

import pytest
from flask import Flask

from a3docklab.application.operations import OperationalMetrics, register_operational_routes
from a3docklab.application.sessions import InteractiveSimulationService, SessionConflict
from a3docklab.application.state import (
    AcceptedCommand,
    ApplicationStateStore,
    DurableSession,
)
from a3docklab.config import load_config


def _scenario() -> dict:
    path = Path(__file__).resolve().parents[1] / "configs/scenarios/blue_moon_side.yaml"
    return {"blue_moon_side": load_config(path)}


def test_session_quotas_are_enforced_per_owner_and_service() -> None:
    identifiers = count(1)
    service = InteractiveSimulationService(
        _scenario(),
        id_factory=lambda: f"session-{next(identifiers)}",
        max_active_sessions=3,
        max_active_sessions_per_owner=2,
    )
    service.create("blue_moon_side", "owner-a")
    service.create("blue_moon_side", "owner-a")
    with pytest.raises(SessionConflict, match="for owner"):
        service.create("blue_moon_side", "owner-a")
    service.create("blue_moon_side", "owner-b")
    with pytest.raises(SessionConflict, match="quota exceeded"):
        service.create("blue_moon_side", "owner-c")


def test_multi_session_load_and_operational_metrics() -> None:
    service = InteractiveSimulationService(
        _scenario(), max_active_sessions=8, max_active_sessions_per_owner=8
    )

    def create_and_step(index: int) -> int:
        created = service.create("blue_moon_side", f"operator-{index}")
        result = service.control(created["session_id"], created["control_token"], "step")
        return int(result["step_index"])

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(create_and_step, range(8))) == [0] * 8
    snapshot = service.operational_snapshot()
    assert snapshot["active_sessions"] == 8
    assert snapshot["simulation_steps"] == 8
    assert snapshot["simulation_rate_hz"] > 0
    assert snapshot["simulated_seconds"] >= 0

    server = Flask(__name__)
    tracker = OperationalMetrics()
    register_operational_routes(server, service, None, tracker)
    client = server.test_client()
    assert client.post(
        "/api/operations/client-metrics", json={"dropped_frames": 3, "reconnects": 2}
    ).status_code == 202
    response = client.get("/api/operations/metrics")
    assert response.status_code == 200
    assert response.json["service"]["active_sessions"] == 8
    assert response.json["http"]["dropped_frames"] == 3
    assert response.json["http"]["reconnects"] == 2
    assert response.json["storage"]["lakebase_ready"] is False
    started = perf_counter()
    for _ in range(50):
        assert client.get("/api/operations/metrics").status_code == 200
    assert (perf_counter() - started) / 50 < 0.250


def test_metrics_reject_negative_client_counters() -> None:
    metrics = OperationalMetrics()
    with pytest.raises(ValueError, match="non-negative"):
        metrics.observe_client(-1, 0)


def test_terminal_session_cleanup_preserves_active_sessions(tmp_path: Path) -> None:
    store = ApplicationStateStore(lambda: sqlite3.connect(tmp_path / "state.db"), "?")
    store.initialize()
    old = datetime.now(UTC) - timedelta(days=60)
    recent = datetime.now(UTC)
    for session_id, status, updated in (
        ("old-terminal", "complete", old),
        ("old-active", "paused", old),
        ("recent-terminal", "terminated", recent),
    ):
        store.create_durable_session(
            DurableSession(
                session_id=session_id,
                scenario_id="blue_moon_side",
                owner="operator@example.com",
                status=status,  # type: ignore[arg-type]
                updated_at_utc=updated,
            )
        )
    store.record_accepted_command(
        AcceptedCommand(
            session_id="old-terminal",
            command_id="command-1",
            idempotency_key="request-1",
            actor="operator@example.com",
            payload={"action": "terminate"},
            accepted_at_utc=old,
        )
    )
    cutoff = datetime.now(UTC) - timedelta(days=30)
    assert store.count_expired_terminal_sessions(cutoff) == 1
    assert store.cleanup_terminal_sessions(cutoff) == 1
    assert store.get_durable_session("old-terminal") is None
    assert store.get_durable_session("old-active") is not None
    assert store.get_durable_session("recent-terminal") is not None
