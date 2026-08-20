"""Plotly/Dash mission replay application with browser-local playback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from a3docklab.telemetry.contracts import BundleManifest
from a3docklab.visualization.replay import ReplayStore

DISPLAY_POINT_BUDGET = 2000
TRUTH_COLUMNS = [
    "event_time_ns",
    "x_m",
    "y_m",
    "z_m",
    "range_m",
    "closing_rate_m_s",
    "phase",
    "propellant_used_kg",
    "corridor_margin_m",
    "keep_out_margin_m",
    "chaser_qw",
    "chaser_qx",
    "chaser_qy",
    "chaser_qz",
    "target_qw",
    "target_qx",
    "target_qy",
    "target_qz",
]


def _records(frame: pd.DataFrame) -> dict[str, list[Any]]:
    return {name: frame[name].tolist() for name in frame.columns}


def _optional_stream(
    store: ReplayStore,
    manifest: BundleManifest,
    stream: str,
    fallback: pd.DataFrame,
) -> pd.DataFrame:
    if stream not in {item.name for item in manifest.streams}:
        return fallback
    return store.query_stream(manifest.run_id, stream, max_points=DISPLAY_POINT_BUDGET)


def load_replay_payload(
    store: ReplayStore, manifest: BundleManifest
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load each display stream once and serialize it for browser-local replay."""
    truth = store.query_stream(manifest.run_id, "truth", max_points=DISPLAY_POINT_BUDGET)
    defaults: dict[str, Any] = {
        "propellant_used_kg": 0.0,
        "corridor_margin_m": 1.0,
        "keep_out_margin_m": 1.0,
        "chaser_qw": 1.0,
        "chaser_qx": 0.0,
        "chaser_qy": 0.0,
        "chaser_qz": 0.0,
        "target_qw": 1.0,
        "target_qx": 0.0,
        "target_qy": 0.0,
        "target_qz": 0.0,
    }
    for column, default in defaults.items():
        if column not in truth:
            truth[column] = default
    truth = truth[TRUTH_COLUMNS]
    empty_health = truth[["event_time_ns"]].copy()
    navigation_fallback = truth.rename(
        columns={"x_m": "estimate_x_m", "y_m": "estimate_y_m", "z_m": "estimate_z_m"}
    )
    navigation = _optional_stream(store, manifest, "navigation_estimates", navigation_fallback)
    actuation = _optional_stream(store, manifest, "actuation", empty_health)
    communications = _optional_stream(store, manifest, "communications", empty_health)
    if "command_response_residual_n" not in actuation:
        actuation["command_response_residual_n"] = 0.0
    if "data_age_ns" not in communications:
        communications["data_age_ns"] = 0
    events = _optional_stream(store, manifest, "events", pd.DataFrame())
    payload = {
        "run_id": manifest.run_id,
        "scenario_id": manifest.scenario_id,
        "terminal_phase": manifest.terminal_phase,
        "schema_version": manifest.schema_version,
        "truth": _records(truth),
        "navigation": _records(navigation),
        "actuation": _records(actuation),
        "communications": _records(communications),
        "events": _records(events),
    }
    options: list[dict[str, Any]] = []
    if not events.empty:
        for row in events.to_dict("records"):
            time_s = int(row["event_time_ns"]) / 1_000_000_000
            options.append(
                {
                    "label": f"{time_s:.1f}s · {row['event_type']} · {row['detail']}",
                    "value": time_s,
                }
            )
    return payload, options


def _safety_geometry(truth: dict[str, list[Any]]) -> tuple[float, float, float]:
    positions = np.column_stack((truth["x_m"], truth["y_m"], truth["z_m"]))
    ranges = np.asarray(truth["range_m"], dtype=float)
    keep_out = np.asarray(truth["keep_out_margin_m"], dtype=float)
    radius = max(0.1, float(np.nanmedian(ranges - keep_out)))
    lateral = np.linalg.norm(positions[:, 1:], axis=1)
    allowed = lateral + np.asarray(truth["corridor_margin_m"], dtype=float)
    axial = np.abs(positions[:, 0])
    valid = axial > 1e-6
    slope = float(np.nanmedian((allowed[valid] - np.nanmin(allowed)) / axial[valid]))
    half_angle = float(np.clip(np.arctan(max(0.01, slope)), np.deg2rad(2), np.deg2rad(30)))
    length = max(radius * 1.5, float(np.nanmax(ranges)))
    direction = 1.0 if float(np.nanmedian(positions[:, 0])) >= 0 else -1.0
    return radius, half_angle, direction * length


def _trajectory_figure(go: Any, payload: dict[str, Any]) -> Any:
    truth = payload["truth"]
    navigation = payload["navigation"]
    radius, half_angle, cone_length = _safety_geometry(truth)
    figure = go.Figure()
    figure.add_trace(
        go.Scatter3d(
            x=truth["x_m"],
            y=truth["y_m"],
            z=truth["z_m"],
            mode="lines",
            name="Orion trail",
            line={
                "color": truth["closing_rate_m_s"],
                "colorscale": "Turbo",
                "width": 7,
                "colorbar": {"title": "Closing<br>rate m/s", "thickness": 12},
            },
            customdata=np.column_stack((truth["phase"], truth["range_m"])),
            hovertemplate="%{customdata[0]}<br>range %{customdata[1]:.2f} m<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter3d(
            x=[truth["x_m"][0]],
            y=[truth["y_m"][0]],
            z=[truth["z_m"][0]],
            mode="markers",
            name="Current position",
            marker={"size": 9, "color": "#5ee7ff", "line": {"width": 3, "color": "white"}},
        )
    )
    figure.add_trace(
        go.Scatter3d(x=[0], y=[0], z=[0], mode="markers", name="Target", marker={"size": 8})
    )
    figure.add_trace(
        go.Scatter3d(
            x=navigation["estimate_x_m"],
            y=navigation["estimate_y_m"],
            z=navigation["estimate_z_m"],
            mode="lines",
            name="Navigation estimate",
            line={"dash": "dot", "color": "#a8b2d1", "width": 3},
        )
    )
    # Dynamic chaser and target docking axes; the browser updates their endpoints.
    figure.add_trace(
        go.Scatter3d(
            x=[truth["x_m"][0]] * 2,
            y=[0, 0],
            z=[0, 0],
            mode="lines",
            name="Chaser port axis",
            line={"color": "#5ee7ff", "width": 8},
        )
    )
    figure.add_trace(
        go.Scatter3d(
            x=[0, 1],
            y=[0, 0],
            z=[0, 0],
            mode="lines",
            name="Target port axis",
            line={"color": "#ffcf5c", "width": 8},
        )
    )
    theta = np.linspace(0, 2 * np.pi, 30)
    phi = np.linspace(0, np.pi, 18)
    sx = radius * np.outer(np.cos(theta), np.sin(phi))
    sy = radius * np.outer(np.sin(theta), np.sin(phi))
    sz = radius * np.outer(np.ones_like(theta), np.cos(phi))
    figure.add_trace(
        go.Surface(
            x=sx,
            y=sy,
            z=sz,
            opacity=0.12,
            showscale=False,
            colorscale=[[0, "#3772ff"], [1, "#3772ff"]],
            name="Keep-out zone",
            hoverinfo="skip",
        )
    )
    cone_x = np.linspace(0, cone_length, 22)
    cone_theta = np.linspace(0, 2 * np.pi, 30)
    cx, ct = np.meshgrid(cone_x, cone_theta)
    cr = np.maximum(0.25, np.abs(cx) * np.tan(half_angle))
    figure.add_trace(
        go.Surface(
            x=cx,
            y=cr * np.cos(ct),
            z=cr * np.sin(ct),
            opacity=0.1,
            showscale=False,
            colorscale=[[0, "#21d19f"], [1, "#21d19f"]],
            name="Approach corridor",
            hoverinfo="skip",
        )
    )
    figure.update_layout(
        template="plotly_dark",
        uirevision=f"trajectory-{payload['run_id']}",
        scene={
            "aspectmode": "data",
            "xaxis_title": "LVLH X (m)",
            "yaxis_title": "LVLH Y (m)",
            "zaxis_title": "LVLH Z (m)",
        },
        title="LVLH rendezvous twin",
        margin={"l": 0, "r": 0, "t": 50, "b": 0},
    )
    return figure


def _timeline_figure(go: Any, payload: dict[str, Any]) -> Any:
    truth = payload["truth"]
    time_s = np.asarray(truth["event_time_ns"], dtype=np.int64) / 1_000_000_000
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=time_s, y=truth["range_m"], name="Range (m)"))
    figure.add_trace(
        go.Scatter(x=time_s, y=truth["closing_rate_m_s"], name="Closing rate (m/s)", yaxis="y2")
    )
    figure.update_layout(
        template="plotly_dark",
        uirevision=f"timeline-{payload['run_id']}",
        title="Range and closing rate",
        yaxis2={"overlaying": "y", "side": "right"},
        shapes=[
            {
                "type": "line",
                "x0": 0,
                "x1": 0,
                "y0": 0,
                "y1": 1,
                "yref": "paper",
                "line": {"color": "#5ee7ff", "width": 2},
            }
        ],
    )
    return figure


def _health_figure(go: Any, payload: dict[str, Any]) -> Any:
    actuation = payload["actuation"]
    communications = payload["communications"]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=np.asarray(actuation["event_time_ns"]) / 1e9,
            y=actuation["command_response_residual_n"],
            name="Command/response residual (N)",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=np.asarray(communications["event_time_ns"]) / 1e9,
            y=np.asarray(communications["data_age_ns"]) / 1e9,
            name="Communications age (s)",
            yaxis="y2",
        )
    )
    figure.update_layout(
        template="plotly_dark",
        uirevision=f"health-{payload['run_id']}",
        title="Actuation and communications health",
        yaxis2={"overlaying": "y", "side": "right"},
        shapes=[
            {
                "type": "line",
                "x0": 0,
                "x1": 0,
                "y0": 0,
                "y1": 1,
                "yref": "paper",
                "line": {"color": "#5ee7ff", "width": 2},
            }
        ],
    )
    return figure


def create_app(store: ReplayStore, live_scenarios: list[dict[str, str]] | None = None) -> Any:
    try:
        import plotly.graph_objects as go  # type: ignore[import-untyped]
        from dash import Dash, Input, Output, State, dcc, html
    except ImportError as exc:
        raise RuntimeError("The replay application requires the 'ui' optional dependency") from exc

    runs = store.list_runs()
    app = Dash(__name__, assets_folder=str(Path(__file__).with_name("assets")))
    app.title = "A3 DockLab Mission Replay"
    empty_live = go.Figure()
    empty_live.add_trace(
        go.Scatter3d(
            x=[],
            y=[],
            z=[],
            mode="lines",
            name="Driven trajectory",
            line={"color": "#5ee7ff", "width": 7},
        )
    )
    empty_live.add_trace(
        go.Scatter3d(
            x=[],
            y=[],
            z=[],
            mode="markers",
            name="Vehicle",
            marker={"size": 10, "color": "#5ee7ff", "line": {"color": "white", "width": 2}},
        )
    )
    empty_live.add_trace(
        go.Scatter3d(
            x=[0],
            y=[0],
            z=[0],
            mode="markers",
            name="Target",
            marker={"size": 8, "color": "#ffcf5c"},
        )
    )
    empty_live.update_layout(
        template="plotly_dark",
        uirevision="live-twin",
        title="Live LVLH docking twin",
        scene={
            "aspectmode": "data",
            "xaxis_title": "LVLH X (m)",
            "yaxis_title": "LVLH Y (m)",
            "zaxis_title": "LVLH Z (m)",
        },
        margin={"l": 0, "r": 0, "t": 50, "b": 0},
    )
    empty_live_history = go.Figure()
    empty_live_history.add_trace(go.Scatter(x=[], y=[], name="Range (m)"))
    empty_live_history.add_trace(go.Scatter(x=[], y=[], name="Closing rate (m/s)", yaxis="y2"))
    empty_live_history.update_layout(
        template="plotly_dark",
        uirevision="live-history",
        title="Live state history",
        yaxis2={"overlaying": "y", "side": "right"},
    )
    live_scenarios = live_scenarios or []
    live_layout = html.Div(
        [
            html.Div(
                [
                    html.H2("Live Lab"),
                    html.P(
                        "Drive the deterministic docking simulation through the guarded command arbiter."
                    ),
                    html.Label("Scenario"),
                    html.Select(
                        id="live-scenario",
                        children=[
                            html.Option(item["name"], value=item["id"], selected=index == 0)
                            for index, item in enumerate(live_scenarios)
                        ],
                    ),
                    html.Button("Create session", id="live-create", className="primary-control"),
                    html.Label("Deterministic fault injection"),
                    html.Select(
                        id="live-fault",
                        children=[
                            html.Option(label, value=value, selected=value == "none")
                            for value, label in (
                                ("none", "None"),
                                ("stale_data", "Stale exchange data"),
                                ("frame_mismatch", "Frame mismatch"),
                                ("actuator_unhealthy", "Actuator unhealthy"),
                                ("lost_acknowledgement", "Lost acknowledgement"),
                                ("duplicated_authority", "Duplicated authority"),
                                ("lost_authority", "Lost authority"),
                                ("shadow_command_mismatch", "Shadow command mismatch"),
                                ("active_owner_failure", "Active owner failure"),
                            )
                        ],
                    ),
                    html.Label("Active controller"),
                    html.Select(
                        id="live-active-policy",
                        children=[
                            html.Option("Human operator", value="", selected=True),
                            html.Option("Reference autopilot", value="reference-autopilot"),
                            html.Option("Station keeping", value="station-keeping"),
                            html.Option("Corridor MPC", value="corridor-mpc"),
                            html.Option("Rule-based mission agent", value="mission-agent"),
                        ],
                    ),
                    html.Label("Shadow policy"),
                    html.Select(
                        id="live-shadow-policy",
                        children=[
                            html.Option("Disabled", value="", selected=True),
                            html.Option("Reference autopilot", value="reference-autopilot"),
                            html.Option("Station keeping", value="station-keeping"),
                            html.Option("Corridor MPC", value="corridor-mpc"),
                            html.Option("Rule-based mission agent", value="mission-agent"),
                        ],
                    ),
                    html.Label("Policy latency budget (ms)"),
                    dcc.Input(id="live-policy-budget", type="number", value=50, min=1, step=1),
                    html.Label("Safe fallback"),
                    html.Select(
                        id="live-policy-fallback",
                        children=[
                            html.Option("Hold", value="hold", selected=True),
                            html.Option("Reference autopilot", value="autopilot"),
                        ],
                    ),
                    html.Div(
                        [
                            html.Button("Run", id="live-resume"),
                            html.Button("Pause", id="live-pause"),
                            html.Button("Step", id="live-step"),
                            html.Button("Reset", id="live-reset"),
                            html.Button(
                                "Terminate", id="live-terminate", className="danger-control"
                            ),
                        ],
                        className="control-row",
                    ),
                    html.Label("Display rate"),
                    html.Select(
                        id="live-rate",
                        children=[
                            html.Option(f"{rate} Hz", value=rate, selected=rate == 10)
                            for rate in (2, 5, 10, 20)
                        ],
                    ),
                    html.H3("Command intent"),
                    html.Select(
                        id="live-mode",
                        children=[
                            html.Option(mode.title(), value=mode, selected=mode == "autopilot")
                            for mode in (
                                "autopilot",
                                "velocity",
                                "hold",
                                "retreat",
                                "capture",
                                "abort",
                            )
                        ],
                    ),
                    html.Label("Desired velocity (m/s)"),
                    html.Div(
                        [
                            dcc.Input(id=f"live-v{axis}", type="number", value=0.0, step=0.01)
                            for axis in "xyz"
                        ],
                        className="vector-row",
                    ),
                    html.Label("Desired torque (N·m)"),
                    html.Div(
                        [
                            dcc.Input(id=f"live-t{axis}", type="number", value=0.0, step=0.1)
                            for axis in "xyz"
                        ],
                        className="vector-row",
                    ),
                    html.Div(id="live-lease", className="lease-status"),
                ],
                className="live-controls",
            ),
            html.Div(
                [
                    html.Div(id="live-kpis", className="kpi-strip"),
                    dcc.Graph(id="live-twin", figure=empty_live),
                    dcc.Graph(id="live-history", figure=empty_live_history),
                    html.Div(
                        [
                            html.Div([html.H3("Safety decision"), html.Pre(id="live-decision")]),
                            html.Div(
                                [html.H3("Policy runtime"), html.Pre(id="live-policy-health")]
                            ),
                            html.Div([html.H3("Shadow policy"), html.Pre(id="live-shadow")]),
                            html.Div([html.H3("Event stream"), html.Pre(id="live-events")]),
                        ],
                        className="live-readouts",
                    ),
                ],
                className="live-stage",
            ),
        ],
        className="live-grid",
    )
    replay_layout = html.Div(
        [
            html.H2("Historical Replay"),
            dcc.Dropdown(
                id="run-selector",
                options=[
                    {"label": f"{run.scenario_id} · {run.run_id[-12:]}", "value": run.run_id}
                    for run in runs
                ],
                value=runs[0].run_id if runs else None,
                placeholder="Select a run",
            ),
            html.Div(
                [
                    html.Button("Play", id="play-button", n_clicks=0),
                    dcc.Dropdown(
                        id="playback-speed",
                        options=[
                            {"label": f"{speed}×", "value": speed} for speed in (1, 5, 10, 50)
                        ],
                        value=10,
                        clearable=False,
                        style={"width": "100px"},
                    ),
                    dcc.Dropdown(
                        id="event-jump", placeholder="Jump to event", style={"minWidth": "360px"}
                    ),
                ],
                style={
                    "display": "flex",
                    "gap": "12px",
                    "alignItems": "center",
                    "marginTop": "12px",
                    "flexWrap": "wrap",
                },
            ),
            dcc.Slider(id="time-slider", min=0.0, max=1.0, step=0.1, value=0.0),
            dcc.Interval(id="playback-timer", interval=100, disabled=True),
            html.Div(id="run-summary", className="kpi-strip"),
            dcc.Graph(id="trajectory-graph"),
            dcc.Graph(id="timeline-graph"),
            dcc.Graph(id="health-graph"),
            html.H2("Mission events"),
            html.Pre(id="event-list"),
        ]
    )
    app.layout = html.Main(
        [
            dcc.Store(id="replay-data"),
            dcc.Store(id="client-clock"),
            html.Header(
                [html.H1("A3 DockLab"), html.Span("Interactive rendezvous & docking laboratory")]
            ),
            dcc.Tabs(
                id="workspace-tabs",
                value="live",
                children=[
                    dcc.Tab(label="Live Lab", value="live", children=live_layout),
                    dcc.Tab(label="Replay", value="replay", children=replay_layout),
                ],
            ),
        ],
        style={
            "maxWidth": "1400px",
            "width": "100%",
            "margin": "0 auto",
            "padding": "16px",
            "fontFamily": "system-ui",
        },
    )

    @app.callback(
        Output("replay-data", "data"),
        Output("time-slider", "max"),
        Output("time-slider", "value"),
        Output("event-jump", "options"),
        Output("trajectory-graph", "figure"),
        Output("timeline-graph", "figure"),
        Output("health-graph", "figure"),
        Output("event-list", "children"),
        Input("run-selector", "value"),
    )
    def configure_replay(run_id: str | None) -> tuple[Any, ...]:
        if run_id is None:
            empty = go.Figure().update_layout(template="plotly_dark", uirevision="empty")
            return None, 1.0, 0.0, [], empty, empty, empty, ""
        manifest = next(run for run in runs if run.run_id == run_id)
        payload, options = load_replay_payload(store, manifest)
        maximum = int(payload["truth"]["event_time_ns"][-1]) / 1_000_000_000
        event_text = "\n".join(option["label"] for option in options)
        return (
            payload,
            maximum,
            0.0,
            options,
            _trajectory_figure(go, payload),
            _timeline_figure(go, payload),
            _health_figure(go, payload),
            event_text,
        )

    app.clientside_callback(
        """function(clicks) { const playing = (clicks || 0) % 2 === 1; return [!playing, playing ? 'Pause' : 'Play']; }""",
        Output("playback-timer", "disabled"),
        Output("play-button", "children"),
        Input("play-button", "n_clicks"),
    )
    app.clientside_callback(
        """function(ticks, eventTime, current, maximum, speed) {
            const trigger = dash_clientside.callback_context.triggered_id;
            if (trigger === 'event-jump' && eventTime !== null) return eventTime;
            if (trigger !== 'playback-timer') return dash_clientside.no_update;
            return Math.min(maximum || 0, (current || 0) + 0.1 * (speed || 1));
        }""",
        Output("time-slider", "value", allow_duplicate=True),
        Input("playback-timer", "n_intervals"),
        Input("event-jump", "value"),
        State("time-slider", "value"),
        State("time-slider", "max"),
        State("playback-speed", "value"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        """function(time, data) { return window.dash_clientside.a3docklab.renderTime(time, data); }""",
        Output("run-summary", "children"),
        Input("time-slider", "value"),
        State("replay-data", "data"),
    )
    return app
