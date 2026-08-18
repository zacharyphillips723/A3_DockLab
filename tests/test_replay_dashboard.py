from datetime import UTC, datetime

import pandas as pd
import plotly.graph_objects as go

from a3docklab.telemetry.contracts import BundleManifest, StreamManifest
from a3docklab.visualization.dashboard import (
    _health_figure,
    _timeline_figure,
    _trajectory_figure,
    load_replay_payload,
)


class RecordingReplayStore:
    def __init__(self) -> None:
        self.queries: list[tuple[str, int | None]] = []
        time = [0, 1_000_000_000]
        self.frames = {
            "truth": pd.DataFrame(
                {
                    "event_time_ns": time,
                    "x_m": [20.0, 10.0],
                    "y_m": [1.0, 0.5],
                    "z_m": [0.0, 0.0],
                    "range_m": [20.02, 10.01],
                    "closing_rate_m_s": [0.2, 0.1],
                    "phase": ["approach", "final"],
                    "propellant_used_kg": [0.0, 1.0],
                    "corridor_margin_m": [1.0, 0.5],
                    "keep_out_margin_m": [10.02, 0.01],
                    "chaser_qw": [1.0, 1.0],
                    "chaser_qx": [0.0, 0.0],
                    "chaser_qy": [0.0, 0.0],
                    "chaser_qz": [0.0, 0.0],
                    "target_qw": [1.0, 1.0],
                    "target_qx": [0.0, 0.0],
                    "target_qy": [0.0, 0.0],
                    "target_qz": [0.0, 0.0],
                }
            ),
            "navigation_estimates": pd.DataFrame(
                {"event_time_ns": time, "estimate_x_m": [20.1, 10.1], "estimate_y_m": [1.0, 0.5], "estimate_z_m": [0.0, 0.0]}
            ),
            "actuation": pd.DataFrame(
                {"event_time_ns": time, "command_response_residual_n": [0.0, 0.1]}
            ),
            "communications": pd.DataFrame({"event_time_ns": time, "data_age_ns": [0, 1]}),
            "events": pd.DataFrame(
                {"event_time_ns": [0], "event_type": ["phase_transition"], "detail": ["approach"]}
            ),
        }

    def list_runs(self) -> list[BundleManifest]:
        return []

    def query_stream(self, run_id: str, stream: str, **kwargs: object) -> pd.DataFrame:
        self.queries.append((stream, kwargs.get("max_points")))
        return self.frames[stream].copy()


def _manifest() -> BundleManifest:
    names = ("truth", "navigation_estimates", "actuation", "communications", "events")
    return BundleManifest(
        schema_version="3.0",
        run_id="run-1",
        scenario_id="test",
        created_at_utc=datetime(2026, 8, 17, tzinfo=UTC),
        configuration_hash="abc",
        terminal_phase="capture",
        streams=[StreamManifest(name=name, path=name, row_count=2, format="test", time_column="event_time_ns") for name in names],
        feature_allowlist_path="",
    )


def test_replay_payload_loads_each_decimated_stream_once() -> None:
    store = RecordingReplayStore()
    payload, events = load_replay_payload(store, _manifest())

    assert [name for name, _ in store.queries] == [
        "truth",
        "navigation_estimates",
        "actuation",
        "communications",
        "events",
    ]
    assert all(budget == 2000 for _, budget in store.queries)
    assert payload["truth"]["target_qw"] == [1.0, 1.0]
    assert events[0]["value"] == 0.0


def test_replay_figures_preserve_ui_and_include_twin_geometry() -> None:
    payload, _ = load_replay_payload(RecordingReplayStore(), _manifest())
    trajectory = _trajectory_figure(go, payload)
    timeline = _timeline_figure(go, payload)
    health = _health_figure(go, payload)

    assert trajectory.layout.uirevision == "trajectory-run-1"
    assert timeline.layout.uirevision == "timeline-run-1"
    assert health.layout.uirevision == "health-run-1"
    assert [trace.name for trace in trajectory.data] == [
        "Orion trail",
        "Current position",
        "Target",
        "Navigation estimate",
        "Chaser port axis",
        "Target port axis",
        "Keep-out zone",
        "Approach corridor",
    ]
    assert len(timeline.layout.shapes) == 1
    assert len(health.layout.shapes) == 1
