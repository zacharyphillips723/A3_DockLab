"""Low-cardinality operational metrics and bounded client telemetry."""

from __future__ import annotations

import math
from collections import Counter, deque
from threading import RLock
from time import perf_counter
from typing import Any

from flask import g, jsonify, request

from a3docklab.application.sessions import InteractiveSimulationService
from a3docklab.application.state import ApplicationStateStore


class OperationalMetrics:
    """Thread-safe in-memory metrics suitable for App logs and health polling."""

    def __init__(self, latency_window: int = 2048) -> None:
        if latency_window < 1:
            raise ValueError("latency_window must be positive")
        self._lock = RLock()
        self._latencies_ms: deque[float] = deque(maxlen=latency_window)
        self._requests: Counter[str] = Counter()
        self._client: Counter[str] = Counter()

    def observe_request(self, method: str, route: str, status: int, latency_ms: float) -> None:
        status_class = f"{status // 100}xx"
        with self._lock:
            self._requests[f"{method} {route} {status_class}"] += 1
            self._latencies_ms.append(max(0.0, latency_ms))

    def observe_client(self, dropped_frames: int, reconnects: int) -> None:
        if dropped_frames < 0 or reconnects < 0:
            raise ValueError("client counters must be non-negative")
        with self._lock:
            self._client["dropped_frames"] += dropped_frames
            self._client["reconnects"] += reconnects

    @staticmethod
    def _percentile(values: list[float], quantile: float) -> float:
        if not values:
            return 0.0
        return values[max(0, math.ceil(len(values) * quantile) - 1)]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            latencies = sorted(self._latencies_ms)
            return {
                "http_requests": dict(self._requests),
                "http_latency_p50_ms": self._percentile(latencies, 0.50),
                "http_latency_p95_ms": self._percentile(latencies, 0.95),
                "http_latency_p99_ms": self._percentile(latencies, 0.99),
                "latency_sample_count": len(latencies),
                "dropped_frames": self._client["dropped_frames"],
                "reconnects": self._client["reconnects"],
            }


def register_operational_routes(
    server: Any,
    service: InteractiveSimulationService,
    state_store: ApplicationStateStore | None,
    metrics: OperationalMetrics | None = None,
) -> OperationalMetrics:
    """Install request instrumentation and operational JSON endpoints."""
    tracker = metrics or OperationalMetrics()

    @server.before_request  # type: ignore[untyped-decorator]
    def begin_request() -> None:
        g.a3docklab_started = perf_counter()

    @server.after_request  # type: ignore[untyped-decorator]
    def finish_request(response: Any) -> Any:
        route = request.url_rule.rule if request.url_rule is not None else "unmatched"
        tracker.observe_request(
            request.method,
            route,
            int(response.status_code),
            (perf_counter() - g.a3docklab_started) * 1000.0,
        )
        return response

    @server.get("/api/operations/metrics")  # type: ignore[untyped-decorator]
    def operational_metrics() -> tuple[object, int]:
        started = perf_counter()
        lakebase_ready = state_store is not None and state_store.healthcheck()
        storage_latency_ms = (perf_counter() - started) * 1000.0 if state_store else None
        return jsonify(
            {
                "schema_version": "1.0",
                "service": service.operational_snapshot(),
                "http": tracker.snapshot(),
                "storage": {
                    "lakebase_ready": lakebase_ready,
                    "healthcheck_latency_ms": storage_latency_ms,
                },
            }
        ), 200

    @server.post("/api/operations/client-metrics")  # type: ignore[untyped-decorator]
    def client_metrics() -> tuple[object, int]:
        payload = request.get_json(silent=True) or {}
        try:
            tracker.observe_client(
                int(payload.get("dropped_frames", 0)), int(payload.get("reconnects", 0))
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"status": "accepted"}), 202

    return tracker
