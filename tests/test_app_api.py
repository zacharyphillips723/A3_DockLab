import json
import sqlite3
from pathlib import Path

from flask import Flask

from a3docklab.application.api import register_state_routes
from a3docklab.application.state import ApplicationStateStore


def _artifact(run_id: str) -> dict[str, str]:
    return {
        "run_id": run_id,
        "schema_version": "3.0",
        "configuration_hash": f"hash-{run_id}",
        "source_uri": f"delta://truth/{run_id}",
    }


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

    comparison_spec = {
        "baseline": _artifact("run-1"),
        "candidate": _artifact("run-2"),
        "alignment": "mission_phase",
    }
    comparison = client.post(
        "/api/comparisons",
        json={
            "name": "Nominal vs candidate",
            "baseline_run_id": "run-1",
            "candidate_run_id": "run-2",
            "alignment": "mission_phase",
            "comparison_spec_json": json.dumps(comparison_spec),
        },
        headers={"X-Forwarded-Email": "operator@example.com"},
    )
    assert comparison.status_code == 201
    restored = client.get("/api/comparisons", headers={"X-Forwarded-Email": "operator@example.com"})
    assert restored.json == [comparison.json]

    view = client.post(
        "/api/views",
        json={
            "name": "Capture window",
            "run_id": "run-1",
            "start_time_ns": 10,
            "end_time_ns": 100,
            "channels": ["range_m", "closing_rate_m_s"],
        },
        headers={"X-Forwarded-Email": "operator@example.com"},
    )
    assert view.status_code == 201
    assert client.get(
        "/api/views", headers={"X-Forwarded-Email": "operator@example.com"}
    ).json == [view.json]

    review = client.post(
        "/api/reviews",
        json={"run_id": "run-1", "status": "approved", "notes": "Evidence verified"},
        headers={"X-Forwarded-Email": "operator@example.com"},
    )
    assert review.status_code == 201
    assert client.get("/api/reviews", query_string={"run_id": "run-1"}).json == [review.json]
    history = client.get("/api/reviews/history", query_string={"run_id": "run-1"})
    assert history.status_code == 200
    assert history.json[0]["status"] == "approved"
    audit = client.get(
        "/api/reviews/audit",
        query_string={"run_id": "run-1"},
        headers={"X-Forwarded-Email": "operator@example.com"},
    )
    assert audit.status_code == 200
    assert audit.json["run_id"] == "run-1"
    assert audit.json["annotations"] == [created.json]
    assert audit.json["saved_views"] == [view.json]
    assert audit.json["current_reviews"] == [review.json]
    assert len(audit.json["review_history"]) == 1
    assert "attachment" in audit.headers["Content-Disposition"]


def test_review_api_validates_ranges_and_status(tmp_path: Path) -> None:
    store = ApplicationStateStore(lambda: sqlite3.connect(tmp_path / "app.db"), "?")
    store.initialize()
    server = Flask(__name__)
    register_state_routes(server, lambda: store)
    client = server.test_client()

    invalid_view = client.post(
        "/api/views",
        json={"name": "Bad", "run_id": "run-1", "start_time_ns": 20, "end_time_ns": 10},
    )
    assert invalid_view.status_code == 400
    invalid_review = client.post(
        "/api/reviews", json={"run_id": "run-1", "status": "not-a-status"}
    )
    assert invalid_review.status_code == 400


def test_app_reports_unavailable_state() -> None:
    server = Flask(__name__)
    register_state_routes(server, lambda: None)
    response = server.test_client().post(
        "/api/annotations", json={"run_id": "run-1", "text": "note"}
    )
    assert response.status_code == 503
