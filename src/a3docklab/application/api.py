"""HTTP endpoints for application health and mutable mission-review state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from flask import jsonify, request

from a3docklab.analysis.contracts import ComparisonSpec
from a3docklab.application.sessions import (
    InteractiveSimulationService,
    SessionConflict,
    SessionNotFound,
    SessionUnauthorized,
)
from a3docklab.application.state import (
    ApplicationStateStore,
    RunReview,
    new_comparison,
    new_saved_view,
)


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

    @server.route("/api/comparisons", methods=["GET", "POST"])  # type: ignore[untyped-decorator]
    def comparisons() -> tuple[object, int]:
        store = state_provider()
        if store is None:
            return jsonify({"error": "Lakebase application state is unavailable"}), 503
        owner = request.headers.get("X-Forwarded-Email", "workspace-smoke-test")
        if request.method == "GET":
            records = store.list_comparisons(owner)
            return jsonify([record.model_dump(mode="json") for record in records]), 200
        payload = request.get_json(silent=True) or {}
        required = ("name", "baseline_run_id", "candidate_run_id", "comparison_spec_json")
        if any(not payload.get(field) for field in required):
            return jsonify({"error": f"{', '.join(required)} are required"}), 400
        try:
            ComparisonSpec.model_validate_json(str(payload["comparison_spec_json"]))
            record = new_comparison(
                owner,
                str(payload["name"]),
                str(payload["baseline_run_id"]),
                str(payload["candidate_run_id"]),
                str(payload.get("alignment", "event_time")),  # type: ignore[arg-type]
                str(payload["comparison_spec_json"]),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        store.save_comparison(record)
        return jsonify(record.model_dump(mode="json")), 201

    @server.route("/api/views", methods=["GET", "POST"])  # type: ignore[untyped-decorator]
    def views() -> tuple[object, int]:
        store = state_provider()
        if store is None:
            return jsonify({"error": "Lakebase application state is unavailable"}), 503
        owner = request.headers.get("X-Forwarded-Email", "workspace-smoke-test")
        if request.method == "GET":
            return jsonify(
                [record.model_dump(mode="json") for record in store.list_views(owner)]
            ), 200
        payload = request.get_json(silent=True) or {}
        if not payload.get("name") or not payload.get("run_id"):
            return jsonify({"error": "name and run_id are required"}), 400
        start = payload.get("start_time_ns")
        end = payload.get("end_time_ns")
        if start is not None and end is not None and int(start) > int(end):
            return jsonify({"error": "start_time_ns must not exceed end_time_ns"}), 400
        record = new_saved_view(
            owner,
            str(payload["name"]),
            str(payload["run_id"]),
            start_time_ns=int(start) if start is not None else None,
            end_time_ns=int(end) if end is not None else None,
            channels=tuple(str(item) for item in payload.get("channels", ())),
        )
        store.save_view(record)
        return jsonify(record.model_dump(mode="json")), 201

    @server.route("/api/reviews", methods=["GET", "POST"])  # type: ignore[untyped-decorator]
    def reviews() -> tuple[object, int]:
        store = state_provider()
        if store is None:
            return jsonify({"error": "Lakebase application state is unavailable"}), 503
        run_id = request.args.get("run_id", "") if request.method == "GET" else ""
        if request.method == "GET":
            if not run_id:
                return jsonify({"error": "run_id is required"}), 400
            return jsonify(
                [record.model_dump(mode="json") for record in store.get_reviews(run_id)]
            ), 200
        payload = request.get_json(silent=True) or {}
        if not payload.get("run_id") or not payload.get("status"):
            return jsonify({"error": "run_id and status are required"}), 400
        reviewer = request.headers.get("X-Forwarded-Email", "workspace-smoke-test")
        try:
            review = RunReview(
                run_id=str(payload["run_id"]),
                reviewer=reviewer,
                status=str(payload["status"]),  # type: ignore[arg-type]
                notes=str(payload.get("notes", "")),
                updated_at_utc=datetime.now(UTC),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        store.upsert_review(review)
        return jsonify(review.model_dump(mode="json")), 201

    @server.get("/api/reviews/history")  # type: ignore[untyped-decorator]
    def review_history() -> tuple[object, int]:
        store = state_provider()
        if store is None:
            return jsonify({"error": "Lakebase application state is unavailable"}), 503
        run_id = request.args.get("run_id", "")
        if not run_id:
            return jsonify({"error": "run_id is required"}), 400
        return jsonify(
            [record.model_dump(mode="json") for record in store.get_review_history(run_id)]
        ), 200

    @server.get("/api/reviews/audit")  # type: ignore[untyped-decorator]
    def review_audit() -> tuple[object, int]:
        store = state_provider()
        if store is None:
            return jsonify({"error": "Lakebase application state is unavailable"}), 503
        run_id = request.args.get("run_id", "")
        if not run_id:
            return jsonify({"error": "run_id is required"}), 400
        owner = request.headers.get("X-Forwarded-Email", "workspace-smoke-test")
        response = jsonify(
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "exported_at_utc": datetime.now(UTC).isoformat(),
                "annotations": [
                    item.model_dump(mode="json") for item in store.list_annotations(run_id)
                ],
                "saved_views": [
                    item.model_dump(mode="json")
                    for item in store.list_views(owner)
                    if item.run_id == run_id
                ],
                "current_reviews": [
                    item.model_dump(mode="json") for item in store.get_reviews(run_id)
                ],
                "review_history": [
                    item.model_dump(mode="json") for item in store.get_review_history(run_id)
                ],
            }
        )
        response.headers["Content-Disposition"] = f'attachment; filename="{run_id}-audit.json"'
        return response, 200


def register_simulation_routes(server: Any, service: InteractiveSimulationService) -> None:
    """Register the browser/model-neutral live simulation API."""

    def token() -> str | None:
        control_token = request.headers.get("X-A3DockLab-Control-Token")
        if control_token:
            return control_token
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

    @server.get("/api/simulations/policies")  # type: ignore[untyped-decorator]
    def simulation_policies() -> tuple[object, int]:
        return jsonify(service.list_policies()), 200

    @server.post("/api/simulations")  # type: ignore[untyped-decorator]
    def create_simulation() -> tuple[object, int]:
        payload = request.get_json(silent=True) or {}
        owner = request.headers.get("X-Forwarded-Email", "local-operator")
        return handle(
            lambda: service.create(
                str(payload.get("scenario_id", "")),
                owner,
                str(payload.get("fault", "none")),
                str(payload["active_policy_id"]) if payload.get("active_policy_id") else None,
                str(payload["shadow_policy_id"]) if payload.get("shadow_policy_id") else None,
                float(payload.get("latency_budget_ms", 50.0)),
                str(payload.get("fallback_mode", "hold")),
                str(payload["model_uri"]) if payload.get("model_uri") else None,
                str(payload.get("model_version", "unknown")),
                str(payload.get("code_revision", "unknown")),
            ),
            201,
        )

    @server.get("/api/simulations/<session_id>")  # type: ignore[untyped-decorator]
    def simulation_status(session_id: str) -> tuple[object, int]:
        return handle(lambda: service.status(session_id))

    @server.post("/api/simulations/<session_id>/restore")  # type: ignore[untyped-decorator]
    def restore_simulation(session_id: str) -> tuple[object, int]:
        owner = request.headers.get("X-Forwarded-Email", "")
        return handle(lambda: service.restore(session_id, owner))

    @server.get("/api/simulations/<session_id>/commands")  # type: ignore[untyped-decorator]
    def simulation_commands(session_id: str) -> tuple[object, int]:
        return handle(lambda: service.command_log(session_id))

    @server.get("/api/simulations/<session_id>/policy-evaluations")  # type: ignore[untyped-decorator]
    def simulation_policy_evaluations(session_id: str) -> tuple[object, int]:
        return handle(lambda: service.policy_evaluations(session_id))

    @server.post("/api/simulations/<session_id>/control")  # type: ignore[untyped-decorator]
    def simulation_control(session_id: str) -> tuple[object, int]:
        payload = request.get_json(silent=True) or {}
        actor = request.headers.get("X-Forwarded-Email")
        idempotency_key = request.headers.get("Idempotency-Key")
        return handle(
            lambda: service.control(
                session_id,
                token(),
                str(payload.get("action", "")),
                payload,
                actor=actor,
                idempotency_key=idempotency_key,
            )
        )
