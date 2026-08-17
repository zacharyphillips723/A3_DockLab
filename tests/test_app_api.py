import sqlite3
from pathlib import Path

from flask import Flask

from a3docklab.application.api import register_state_routes
from a3docklab.application.state import ApplicationStateStore


def test_app_health_and_annotation_round_trip(tmp_path: Path) -> None:
    store = ApplicationStateStore(lambda: sqlite3.connect(tmp_path / "app.db"), "?")
    store.initialize()
    server = Flask(__name__)
    register_state_routes(server, lambda: store)
    client = server.test_client()

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json == {"status": "ok", "lakebase_ready": True}
    created = client.post(
        "/api/annotations",
        json={"run_id": "run-1", "text": "Inspect capture", "event_time_ns": 42},
        headers={"X-Forwarded-Email": "operator@example.com"},
    )
    assert created.status_code == 201
    assert created.json["author"] == "operator@example.com"
    listed = client.get("/api/annotations", query_string={"run_id": "run-1"})
    assert listed.status_code == 200
    assert listed.json == [created.json]


def test_app_reports_unavailable_state() -> None:
    server = Flask(__name__)
    register_state_routes(server, lambda: None)
    response = server.test_client().post(
        "/api/annotations", json={"run_id": "run-1", "text": "note"}
    )
    assert response.status_code == 503
