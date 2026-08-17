"""HTTP endpoints for application health and mutable mission-review state."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import jsonify, request

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
