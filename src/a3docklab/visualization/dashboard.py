"""Plotly/Dash mission replay application."""

from __future__ import annotations

from typing import Any

from a3docklab.visualization.replay import ReplayStore


def create_app(store: ReplayStore) -> Any:
    try:
        import plotly.graph_objects as go  # type: ignore[import-untyped]
        from dash import Dash, Input, Output, State, ctx, dcc, html
    except ImportError as exc:
        raise RuntimeError("The replay application requires the 'ui' optional dependency") from exc

    runs = store.list_runs()
    app = Dash(__name__)
    app.title = "A3 DockLab Mission Replay"
    app.layout = html.Main(
        [
            html.H1("A3 DockLab Mission Replay", style={"fontSize": "clamp(2rem, 5vw, 4rem)"}),
            dcc.Dropdown(
                id="run-selector",
                options=[
                    {"label": f"{run.scenario_id} · {run.run_id[-12:]}", "value": run.run_id}
                    for run in runs
                ],
                value=runs[0].run_id if runs else None,
                placeholder="Select a run",
                style={"width": "100%"},
            ),
            dcc.Dropdown(
                id="compare-selector",
                options=[
                    {
                        "label": f"Compare: {run.scenario_id} · {run.run_id[-12:]}",
                        "value": run.run_id,
                    }
                    for run in runs
                ],
                value=None,
                placeholder="Optional comparison run",
                style={"width": "100%", "marginTop": "8px"},
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
                        style={"width": "100px", "display": "inline-block"},
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
            html.Div(
                dcc.Slider(id="time-slider", min=0.0, max=1.0, step=0.1, value=0.0),
                style={"padding": "12px 8px 0"},
            ),
            dcc.Interval(id="playback-timer", interval=500, disabled=True),
            html.Div(id="run-summary"),
            dcc.Graph(id="trajectory-graph"),
            dcc.Graph(id="timeline-graph"),
            dcc.Graph(id="health-graph"),
            html.H2("Mission events"),
            html.Pre(id="event-list"),
        ],
        style={
            "maxWidth": "1400px",
            "width": "100%",
            "margin": "0 auto",
            "padding": "16px",
            "boxSizing": "border-box",
            "overflowX": "hidden",
            "fontFamily": "system-ui",
        },
    )

    @app.callback(
        Output("time-slider", "max"),
        Output("time-slider", "value"),
        Output("event-jump", "options"),
        Input("run-selector", "value"),
    )
    def configure_replay(run_id: str | None) -> tuple[float, float, list[dict[str, Any]]]:
        if run_id is None:
            return 1.0, 0.0, []
        truth = store.query_stream(run_id, "truth", columns=["event_time_ns"])
        events = store.query_stream(run_id, "events")
        maximum = float(truth["event_time_ns"].to_numpy(dtype="int64")[-1] / 1_000_000_000)
        event_times = events["event_time_ns"].to_numpy(dtype="int64")
        event_types = events["event_type"].astype(str).tolist()
        event_details = events["detail"].astype(str).tolist()
        options = [
            {
                "label": f"{time_ns / 1_000_000_000:.1f}s · {event_type} · {detail}",
                "value": time_ns / 1_000_000_000,
            }
            for time_ns, event_type, detail in zip(
                event_times, event_types, event_details, strict=True
            )
        ]
        return maximum, 0.0, options

    @app.callback(
        Output("playback-timer", "disabled"),
        Output("play-button", "children"),
        Input("play-button", "n_clicks"),
    )
    def toggle_playback(clicks: int) -> tuple[bool, str]:
        playing = clicks % 2 == 1
        return not playing, "Pause" if playing else "Play"

    @app.callback(
        Output("time-slider", "value", allow_duplicate=True),
        Input("playback-timer", "n_intervals"),
        Input("event-jump", "value"),
        State("time-slider", "value"),
        State("time-slider", "max"),
        State("playback-speed", "value"),
        prevent_initial_call=True,
    )
    def advance_replay(
        _ticks: int,
        event_time_s: float | None,
        current_s: float,
        maximum_s: float,
        speed: int,
    ) -> float:
        if ctx.triggered_id == "event-jump" and event_time_s is not None:
            return event_time_s
        return min(maximum_s, current_s + 0.5 * speed)

    @app.callback(
        Output("run-summary", "children"),
        Output("trajectory-graph", "figure"),
        Output("timeline-graph", "figure"),
        Output("health-graph", "figure"),
        Output("event-list", "children"),
        Input("run-selector", "value"),
        Input("time-slider", "value"),
        Input("compare-selector", "value"),
    )
    def render_run(
        run_id: str | None, current_time_s: float, compare_run_id: str | None
    ) -> tuple[Any, Any, Any, Any, str]:
        if run_id is None:
            empty = go.Figure().update_layout(template="plotly_dark")
            return "No run selected", empty, empty, empty, ""
        manifest = next(run for run in store.list_runs() if run.run_id == run_id)
        end_ns = int(current_time_s * 1_000_000_000)
        truth = store.query_stream(run_id, "truth", end_ns=end_ns, max_points=2000)
        available = {stream.name for stream in manifest.streams}
        if "navigation_estimates" in available:
            navigation = store.query_stream(
                run_id, "navigation_estimates", end_ns=end_ns, max_points=2000
            )
        else:
            navigation = truth.rename(
                columns={"x_m": "estimate_x_m", "y_m": "estimate_y_m", "z_m": "estimate_z_m"}
            )
        if "actuation" in available:
            actuation = store.query_stream(run_id, "actuation", end_ns=end_ns, max_points=2000)
        else:
            actuation = truth[["event_time_ns"]].copy()
            actuation["command_response_residual_n"] = 0.0
        if "communications" in available:
            communications = store.query_stream(
                run_id, "communications", end_ns=end_ns, max_points=2000
            )
        else:
            communications = truth[["event_time_ns"]].copy()
            communications["data_age_ns"] = 0
        events = store.query_stream(run_id, "events")
        trajectory = go.Figure(
            go.Scatter3d(x=truth["x_m"], y=truth["y_m"], z=truth["z_m"], mode="lines", name="Orion")
        )
        trajectory.add_trace(go.Scatter3d(x=[0], y=[0], z=[0], mode="markers", name="Target"))
        trajectory.add_trace(
            go.Scatter3d(
                x=navigation["estimate_x_m"],
                y=navigation["estimate_y_m"],
                z=navigation["estimate_z_m"],
                mode="lines",
                name="Navigation estimate",
                line={"dash": "dot"},
            )
        )
        trajectory.update_layout(
            template="plotly_dark", scene_aspectmode="data", title="LVLH trajectory"
        )
        timeline = go.Figure()
        time_s = truth["event_time_ns"] / 1_000_000_000
        timeline.add_trace(go.Scatter(x=time_s, y=truth["range_m"], name="Range (m)"))
        timeline.add_trace(
            go.Scatter(x=time_s, y=truth["closing_rate_m_s"], name="Closing rate (m/s)", yaxis="y2")
        )
        if compare_run_id is not None and compare_run_id != run_id:
            comparison = store.query_stream(compare_run_id, "truth", end_ns=end_ns, max_points=2000)
            comparison_time_s = comparison["event_time_ns"] / 1_000_000_000
            timeline.add_trace(
                go.Scatter(
                    x=comparison_time_s,
                    y=comparison["range_m"],
                    name="Comparison range (m)",
                    line={"dash": "dash"},
                )
            )
        timeline.update_layout(
            template="plotly_dark",
            title="Range and closing rate",
            yaxis2={"overlaying": "y", "side": "right"},
        )
        health = go.Figure()
        health.add_trace(
            go.Scatter(
                x=actuation["event_time_ns"] / 1_000_000_000,
                y=actuation["command_response_residual_n"],
                name="Command/response residual (N)",
            )
        )
        health.add_trace(
            go.Scatter(
                x=communications["event_time_ns"] / 1_000_000_000,
                y=communications["data_age_ns"] / 1_000_000_000,
                name="Communications age (s)",
                yaxis="y2",
            )
        )
        health.update_layout(
            template="plotly_dark",
            title="Actuation and communications health",
            yaxis2={"overlaying": "y", "side": "right"},
        )
        summary = (
            f"{manifest.scenario_id} · terminal phase: {manifest.terminal_phase} · "
            f"schema {manifest.schema_version} · replay time {current_time_s:.1f}s · "
            f"phase {truth['phase'].iat[-1]}"
        )
        event_times = events["event_time_ns"].to_numpy(dtype="int64")
        event_types = events["event_type"].astype(str).tolist()
        event_details = events["detail"].astype(str).tolist()
        event_text = "\n".join(
            f"{time_ns / 1_000_000_000:8.1f}s  {event_type:26s} {detail}"
            for time_ns, event_type, detail in zip(
                event_times, event_types, event_details, strict=True
            )
        )
        return summary, trajectory, timeline, health, event_text

    return app
