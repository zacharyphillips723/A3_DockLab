import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from flask import Flask

from a3docklab.application.api import register_simulation_routes
from a3docklab.application.sessions import (
    InteractiveSimulationService,
    SessionConflict,
    SessionUnauthorized,
)
from a3docklab.application.state import ApplicationStateStore
from a3docklab.config import load_config


@pytest.fixture()
def service() -> InteractiveSimulationService:
    path = Path(__file__).resolve().parents[1] / "configs/scenarios/blue_moon_side.yaml"
    return InteractiveSimulationService(
        {"blue_moon_side": load_config(path)},
        id_factory=lambda: "session-1",
        token_factory=lambda: "lease-1",
    )


def _durable_service(
    tmp_path: Path, now: list[datetime]
) -> tuple[InteractiveSimulationService, ApplicationStateStore]:
    store = ApplicationStateStore(lambda: sqlite3.connect(tmp_path / "state.db"), "?")
    store.initialize()
    path = Path(__file__).resolve().parents[1] / "configs/scenarios/blue_moon_side.yaml"
    service = InteractiveSimulationService(
        {"blue_moon_side": load_config(path)},
        id_factory=lambda: "session-1",
        token_factory=lambda: "lease-1",
        state_store=store,
        lease_duration=timedelta(seconds=30),
        clock=lambda: now[0],
        holder_id="app-1",
    )
    return service, store


def test_session_lifecycle_and_reconnect_checkpoint(
    service: InteractiveSimulationService,
) -> None:
    created = service.create("blue_moon_side", "pilot@example.com")
    assert created["lifecycle"] == "paused"
    assert created["control_token"] == "lease-1"

    first = service.control("session-1", "lease-1", "step", {"intent": {"mode": "hold"}})
    assert first["step_index"] == 0
    assert first["frame"]["decision"]["requested_mode"] == "hold"
    assert first["checkpoint"]["command_count"] == 1
    assert service.command_log("session-1")["commands"][0]["mode"] == "hold"

    service.control("session-1", "lease-1", "resume")
    advanced = service.control("session-1", "lease-1", "advance", {"intent": {"mode": "autopilot"}})
    assert advanced["lifecycle"] == "running"
    assert advanced["step_index"] == 1
    assert service.status("session-1")["checkpoint"] == advanced["checkpoint"]

    service.control("session-1", "lease-1", "pause")
    with pytest.raises(SessionConflict, match="resume"):
        service.control("session-1", "lease-1", "advance")


def test_control_lease_prevents_a_second_driver(
    service: InteractiveSimulationService,
) -> None:
    service.create("blue_moon_side", "pilot@example.com")
    with pytest.raises(SessionUnauthorized, match="control lease"):
        service.control("session-1", "another-token", "step")


def test_durable_service_persists_lifecycle_and_idempotent_control(tmp_path: Path) -> None:
    now = [datetime.now(UTC)]
    service, store = _durable_service(tmp_path, now)
    created = service.create("blue_moon_side", "pilot@example.com")
    durable = store.get_durable_session("session-1")
    assert durable is not None
    assert durable.owner == "pilot@example.com"
    assert durable.lease_holder == "app-1"

    stepped = service.control(
        "session-1",
        created["control_token"],
        "step",
        {"intent": {"mode": "hold"}},
        actor="pilot@example.com",
        idempotency_key="request-1",
    )
    duplicate = service.control(
        "session-1",
        created["control_token"],
        "step",
        {"intent": {"mode": "hold"}},
        actor="pilot@example.com",
        idempotency_key="request-1",
    )
    assert duplicate["step_index"] == stepped["step_index"] == 0
    durable = store.get_durable_session("session-1")
    assert durable is not None
    assert durable.checkpoint is not None
    assert durable.checkpoint["checkpoint"]["command_count"] == 1


def test_durable_service_rejects_wrong_actor_expired_lease_and_stale_writer(
    tmp_path: Path,
) -> None:
    now = [datetime.now(UTC)]
    service, store = _durable_service(tmp_path, now)
    created = service.create("blue_moon_side", "pilot@example.com")

    with pytest.raises(SessionUnauthorized, match="owner identity"):
        service.control(
            "session-1",
            created["control_token"],
            "step",
            actor="intruder@example.com",
            idempotency_key="wrong-actor",
        )

    durable = store.get_durable_session("session-1")
    assert durable is not None
    store.update_session_checkpoint("session-1", "paused", {"external": True}, durable.version)
    with pytest.raises(SessionUnauthorized, match="expired or stale"):
        service.control(
            "session-1",
            created["control_token"],
            "step",
            actor="pilot@example.com",
            idempotency_key="stale-writer",
        )

    expired_path = tmp_path / "expired"
    expired_path.mkdir()
    service, _ = _durable_service(expired_path, now)
    created = service.create("blue_moon_side", "pilot@example.com")
    now[0] += timedelta(seconds=31)
    with pytest.raises(SessionUnauthorized, match="expired or stale"):
        service.control(
            "session-1",
            created["control_token"],
            "step",
            actor="pilot@example.com",
            idempotency_key="expired",
        )


def test_durable_control_api_requires_identity_and_idempotency_key(tmp_path: Path) -> None:
    now = [datetime.now(UTC)]
    service, _ = _durable_service(tmp_path, now)
    server = Flask(__name__)
    register_simulation_routes(server, service)
    client = server.test_client()
    created = client.post(
        "/api/simulations",
        json={"scenario_id": "blue_moon_side"},
        headers={"X-Forwarded-Email": "pilot@example.com"},
    )
    authorization = {"Authorization": f"Bearer {created.json['control_token']}"}

    assert (
        client.post(
            "/api/simulations/session-1/control",
            json={"action": "step"},
            headers=authorization,
        ).status_code
        == 403
    )
    missing_key = client.post(
        "/api/simulations/session-1/control",
        json={"action": "step"},
        headers={**authorization, "X-Forwarded-Email": "pilot@example.com"},
    )
    assert missing_key.status_code == 400
    headers = {
        **authorization,
        "X-Forwarded-Email": "pilot@example.com",
        "Idempotency-Key": "request-1",
    }
    first = client.post(
        "/api/simulations/session-1/control", json={"action": "step"}, headers=headers
    )
    duplicate = client.post(
        "/api/simulations/session-1/control", json={"action": "step"}, headers=headers
    )
    assert first.status_code == duplicate.status_code == 200
    assert first.json["step_index"] == duplicate.json["step_index"] == 0


def test_live_api_round_trip_and_lease_enforcement(
    service: InteractiveSimulationService,
) -> None:
    server = Flask(__name__)
    register_simulation_routes(server, service)
    client = server.test_client()

    scenarios = client.get("/api/simulations/scenarios")
    assert scenarios.status_code == 200
    assert scenarios.json[0]["id"] == "blue_moon_side"
    created = client.post(
        "/api/simulations",
        json={"scenario_id": "blue_moon_side"},
        headers={"X-Forwarded-Email": "pilot@example.com"},
    )
    assert created.status_code == 201
    assert created.json["control_token"] == "lease-1"

    denied = client.post("/api/simulations/session-1/control", json={"action": "step"})
    assert denied.status_code == 403
    stepped = client.post(
        "/api/simulations/session-1/control",
        json={
            "action": "step",
            "intent": {"mode": "velocity", "desired_velocity_m_s": [-0.1, 0.0, 0.0]},
        },
        headers={"Authorization": "Bearer lease-1"},
    )
    assert stepped.status_code == 200
    assert stepped.json["frame"]["decision"]["requested_mode"] == "velocity"
    assert "NaN" not in stepped.text
    reconnected = client.get("/api/simulations/session-1")
    assert reconnected.json["checkpoint"]["step_index"] == 0
    commands = client.get("/api/simulations/session-1/commands")
    assert commands.json["commands"][0]["desired_velocity_m_s"] == [-0.1, 0.0, 0.0]


def test_policy_catalog_and_evaluation_api(service: InteractiveSimulationService) -> None:
    server = Flask(__name__)
    register_simulation_routes(server, service)
    client = server.test_client()
    policies = client.get("/api/simulations/policies")
    assert {row["id"] for row in policies.json} >= {"corridor-mpc", "mission-agent"}
    created = client.post(
        "/api/simulations",
        json={"scenario_id": "blue_moon_side", "active_policy_id": "corridor-mpc"},
    )
    client.post(
        "/api/simulations/session-1/control",
        json={"action": "step"},
        headers={"Authorization": f"Bearer {created.json['control_token']}"},
    )
    evaluations = client.get("/api/simulations/session-1/policy-evaluations")
    assert evaluations.status_code == 200
    assert evaluations.json["evaluations"][0]["policy"]["policy_id"] == "corridor-mpc"


def test_fault_selection_is_validated(service: InteractiveSimulationService) -> None:
    created = service.create("blue_moon_side", "pilot@example.com", "stale_data")
    assert created["fault"] == "stale_data"
    with pytest.raises(ValueError, match="unsupported fault"):
        service.create("blue_moon_side", "pilot@example.com", "not-a-fault")


def test_shadow_policy_is_exposed_without_taking_authority(
    service: InteractiveSimulationService,
) -> None:
    created = service.create(
        "blue_moon_side", "pilot@example.com", shadow_policy_id="reference-autopilot"
    )
    stepped = service.control(
        created["session_id"],
        created["control_token"],
        "step",
        {"intent": {"mode": "hold"}},
    )

    assert stepped["frame"]["decision"]["requested_mode"] == "hold"
    assert stepped["frame"]["shadow_decision"]["requested_mode"] == "autopilot"
    assert stepped["frame"]["shadow_policy"]["policy_version"] == "1.0.0"
    assert stepped["frame"]["state"]["command_requested_mode"] == "hold"


def test_active_policy_and_evaluation_history(service: InteractiveSimulationService) -> None:
    created = service.create(
        "blue_moon_side",
        "pilot@example.com",
        active_policy_id="corridor-mpc",
        shadow_policy_id="station-keeping",
        latency_budget_ms=100,
        fallback_mode="hold",
    )
    stepped = service.control(created["session_id"], created["control_token"], "step")
    history = service.policy_evaluations(created["session_id"])

    assert stepped["frame"]["active_policy_evaluation"]["health"] == "healthy"
    assert stepped["frame"]["shadow_policy_evaluation"]["health"] == "healthy"
    assert stepped["frame"]["decision"]["driver_id"] == "corridor-mpc"
    assert [row["authority"] for row in history["evaluations"]] == ["active", "shadow"]
