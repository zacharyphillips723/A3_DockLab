"""HTTP endpoints for application health and mutable mission-review state."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import jsonify, request

from a3docklab.application.sessions import (
    InteractiveSimulationService,
    SessionConflict,
    SessionNotFound,
    SessionUnauthorized,
)
from a3docklab.application.state import ApplicationStateStore


def register_state_routes(
    server: Any, state_provider: Callable[[], ApplicationStateStore | None]
) -> None:
    @server.get("/api/health")  # type: ignore[untyped-decorator]
    def health() -> tuple[object, int]:
        store = state_provider()
        lakebase_ready = store is not None and store.healthcheck()
        return jsonify({"status": "ok", "lakebase_ready": lakebase_ready}), 200

    @server.route("/api/annotations", methods=["GET", "POST"])  # type: ignore[untyped-decorator]
    def annotations() -> tuple[object, int]:
        store = state_provider()
        if store is None:
            return jsonify({"error": "Lakebase application state is unavailable"}), 503
        if request.method == "GET":
            run_id = request.args.get("run_id", "")
            if not run_id:
                return jsonify({"error": "run_id is required"}), 400
            records = store.list_annotations(run_id)
            return jsonify([record.model_dump(mode="json") for record in records]), 200
        payload = request.get_json(silent=True) or {}
        if not payload.get("run_id") or not payload.get("text"):
            return jsonify({"error": "run_id and text are required"}), 400
        author = request.headers.get("X-Forwarded-Email", "workspace-smoke-test")
        record = store.add_annotation(
            str(payload["run_id"]),
            author,
            str(payload["text"]),
            int(payload["event_time_ns"]) if payload.get("event_time_ns") is not None else None,
        )
        return jsonify(record.model_dump(mode="json")), 201


def register_simulation_routes(server: Any, service: InteractiveSimulationService) -> None:
    """Register the browser/model-neutral live simulation API."""

    def token() -> str | None:
        value = request.headers.get("Authorization", "")
        return value[7:] if value.startswith("Bearer ") else None

    def handle(operation: Callable[[], Any], success: int = 200) -> tuple[object, int]:
        try:
            return jsonify(operation()), success
        except SessionNotFound as exc:
            return jsonify({"error": str(exc)}), 404
        except SessionUnauthorized as exc:
            return jsonify({"error": str(exc)}), 403
        except SessionConflict as exc:
            return jsonify({"error": str(exc)}), 409
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @server.get("/api/simulations/scenarios")  # type: ignore[untyped-decorator]
    def simulation_scenarios() -> tuple[object, int]:
        return jsonify(service.list_scenarios()), 200

    @server.post("/api/simulations")  # type: ignore[untyped-decorator]
    def create_simulation() -> tuple[object, int]:
        payload = request.get_json(silent=True) or {}
        owner = request.headers.get("X-Forwarded-Email", "local-operator")
        return handle(
            lambda: service.create(
                str(payload.get("scenario_id", "")),
                owner,
                str(payload.get("fault", "none")),
                str(payload["shadow_policy_id"]) if payload.get("shadow_policy_id") else None,
            ),
            201,
        )

    @server.get("/api/simulations/<session_id>")  # type: ignore[untyped-decorator]
    def simulation_status(session_id: str) -> tuple[object, int]:
        return handle(lambda: service.status(session_id))

    @server.get("/api/simulations/<session_id>/commands")  # type: ignore[untyped-decorator]
    def simulation_commands(session_id: str) -> tuple[object, int]:
        return handle(lambda: service.command_log(session_id))

    @server.post("/api/simulations/<session_id>/control")  # type: ignore[untyped-decorator]
    def simulation_control(session_id: str) -> tuple[object, int]:
        payload = request.get_json(silent=True) or {}
        return handle(
            lambda: service.control(session_id, token(), str(payload.get("action", "")), payload)
        )
