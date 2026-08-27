"""Plotly/Dash mission replay application with browser-local playback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

import numpy as np
import pandas as pd

from a3docklab.analysis.comparison import ComparisonResult, compare_runs, parse_schema_mapping
from a3docklab.analysis.contracts import AnalysisBudget
from a3docklab.analysis.risk import RiskQueryResult, RiskSampleMaterializer, RiskStore
from a3docklab.application.state import ApplicationStateStore, new_comparison
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


def _risk_figures(go: Any, result: RiskQueryResult, point_budget: int) -> tuple[Any, Any, Any]:
    runs = result.runs
    distributions = go.Figure()
    distributions.add_trace(
        go.Histogram(x=runs["closest_approach_m"], name="Closest approach (m)", opacity=0.72)
    )
    distributions.add_trace(
        go.Histogram(x=runs["propellant_used_kg"], name="Propellant used (kg)", opacity=0.72)
    )
    distributions.update_layout(
        template="plotly_dark",
        barmode="overlay",
        title="Outcome distributions",
        uirevision=f"risk-distributions-{result.ensemble.ensemble_id}",
    )

    convergence = result.convergence
    if len(convergence) > point_budget:
        convergence = convergence.iloc[
            np.unique(np.linspace(0, len(convergence) - 1, point_budget, dtype=int))
        ]
    convergence_figure = go.Figure()
    convergence_figure.add_trace(
        go.Scatter(
            x=convergence["sample_count"], y=convergence["capture_rate"], name="Capture rate"
        )
    )
    convergence_figure.add_trace(
        go.Scatter(x=convergence["sample_count"], y=convergence["abort_rate"], name="Abort rate")
    )
    convergence_figure.update_layout(
        template="plotly_dark",
        title="Monte Carlo convergence",
        yaxis_tickformat=".0%",
        uirevision=f"risk-convergence-{result.ensemble.ensemble_id}",
    )

    sensitivity = result.sensitivity.head(12).sort_values("importance")
    sensitivity_figure = go.Figure(
        go.Bar(x=sensitivity["importance"], y=sensitivity["parameter"], orientation="h")
    )
    sensitivity_figure.update_layout(
        template="plotly_dark",
        title="Input sensitivity (screening correlation)",
        xaxis_title="Absolute correlation",
        margin={"l": 190},
        uirevision=f"risk-sensitivity-{result.ensemble.ensemble_id}",
    )
    return distributions, convergence_figure, sensitivity_figure


def _risk_detail_figures(go: Any, result: RiskQueryResult) -> tuple[Any, Any]:
    runs = result.runs
    contact = go.Figure()
    metrics = (
        ("contact_closing_rate_m_s", "Closing rate (m/s)"),
        ("contact_lateral_offset_m", "Lateral offset (m)"),
        ("contact_angular_error_deg", "Angular error (deg)"),
    )
    for column, label in metrics:
        if column in runs and runs[column].notna().any():
            contact.add_trace(go.Box(y=runs[column].dropna(), name=label, boxpoints="outliers"))
    if not contact.data:
        contact.add_annotation(
            text="Contact metrics are unavailable in this older ensemble", showarrow=False
        )
    contact.update_layout(
        template="plotly_dark",
        title="Contact conditions",
        uirevision=f"risk-contact-{result.ensemble.ensemble_id}",
    )

    faults = result.fault_sensitivity.sort_values("abort_rate")
    fault_figure = go.Figure()
    fault_figure.add_trace(
        go.Bar(
            x=faults["abort_rate"],
            y=faults["fault"],
            orientation="h",
            customdata=np.column_stack((faults["run_count"], faults["capture_rate"])),
            hovertemplate="abort %{x:.1%}<br>capture %{customdata[1]:.1%}<br>runs %{customdata[0]}<extra></extra>",
        )
    )
    fault_figure.update_layout(
        template="plotly_dark",
        title="Outcome by sampled fault",
        xaxis_tickformat=".0%",
        uirevision=f"risk-faults-{result.ensemble.ensemble_id}",
    )
    return contact, fault_figure


def _outlier_table(
    html: Any,
    result: RiskQueryResult,
    replay_run_ids: set[str],
    can_materialize: bool,
) -> Any:
    runs = result.runs.copy()
    runs["risk_rank"] = (
        runs["closest_approach_m"].rank(pct=True)
        + runs["propellant_used_kg"].rank(pct=True)
        + runs["abort"].astype(float)
    )
    outliers = runs.nlargest(min(10, len(runs)), "risk_rank")
    headers = ("Sample", "Outcome", "Closest approach", "Propellant", "Replay")
    body = []
    for _, row in outliers.iterrows():
        run_id = str(row.get("run_id", ""))
        if run_id in replay_run_ids:
            replay = html.A("Open replay", href=f"/?workspace=replay&run_id={run_id}")
        elif can_materialize:
            replay = html.A(
                "Materialize & replay",
                href=(
                    f"/api/risk/materialize?ensemble_id={quote(result.ensemble.ensemble_id)}"
                    f"&sample_index={int(row['sample_index'])}"
                ),
            )
        else:
            replay = html.Span("Not materialized", className="muted")
        body.append(
            html.Tr(
                [
                    html.Td(str(int(row["sample_index"]))),
                    html.Td(
                        "Abort"
                        if bool(row["abort"])
                        else "Capture"
                        if bool(row["capture_success"])
                        else str(row["terminal_phase"])
                    ),
                    html.Td(f"{float(row['closest_approach_m']):.3f} m"),
                    html.Td(f"{float(row['propellant_used_kg']):.2f} kg"),
                    html.Td(replay),
                ]
            )
        )
    return html.Table(
        [html.Thead(html.Tr([html.Th(item) for item in headers])), html.Tbody(body)],
        className="outlier-table",
    )


def _risk_kpis(html: Any, result: RiskQueryResult) -> list[Any]:
    summary = result.summary
    items = (
        ("Capture", f"{float(summary['capture_rate']):.1%}"),
        ("Abort", f"{float(summary['abort_rate']):.1%}"),
        ("Samples", f"{int(summary['sample_count']):,}"),
        ("P05 approach", f"{float(summary['p05_closest_approach_m']):.3f} m"),
        ("P95 propellant", f"{float(summary['p95_propellant_used_kg']):.2f} kg"),
    )
    return [
        html.Div([html.Span(label), html.Strong(value)], className="kpi-card")
        for label, value in items
    ]


def _comparison_figures(go: Any, result: ComparisonResult) -> tuple[Any, Any, Any]:
    trajectory = go.Figure()
    for name, frame, color in (
        ("Baseline", result.baseline, "#5ee7ff"),
        ("Candidate", result.candidate, "#ffcf5c"),
    ):
        trajectory.add_trace(
            go.Scatter3d(
                x=frame["x_m"],
                y=frame["y_m"],
                z=frame["z_m"],
                mode="lines",
                name=name,
                line={"color": color, "width": 7},
                customdata=np.column_stack((frame["phase"], frame["range_m"])),
                hovertemplate="%{customdata[0]}<br>range %{customdata[1]:.3f} m<extra></extra>",
            )
        )
    trajectory.add_trace(go.Scatter3d(x=[0], y=[0], z=[0], mode="markers", name="Target"))
    trajectory.update_layout(
        template="plotly_dark",
        title="Trajectory overlay",
        scene={"aspectmode": "data"},
        uirevision=f"compare-trajectory-{result.baseline_manifest.run_id}-{result.candidate_manifest.run_id}",
        margin={"l": 0, "r": 0, "t": 50, "b": 0},
    )

    aligned = result.aligned
    timeline = go.Figure()
    for prefix, label, color in (
        ("baseline", "Baseline", "#5ee7ff"),
        ("candidate", "Candidate", "#ffcf5c"),
    ):
        timeline.add_trace(
            go.Scatter(
                x=aligned["comparison_axis"],
                y=aligned[f"{prefix}_range_m"],
                name=f"{label} range",
                line={"color": color},
            )
        )
        timeline.add_trace(
            go.Scatter(
                x=aligned["comparison_axis"],
                y=aligned[f"{prefix}_closing_rate_m_s"],
                name=f"{label} closing rate",
                line={"color": color, "dash": "dot"},
                yaxis="y2",
            )
        )
    timeline.update_layout(
        template="plotly_dark",
        title="Aligned range and closing rate",
        xaxis_title=str(aligned["alignment_label"].iloc[0]),
        yaxis2={"overlaying": "y", "side": "right"},
        uirevision=f"compare-timeline-{result.spec.alignment}-{result.baseline_manifest.run_id}-{result.candidate_manifest.run_id}",
    )

    safety = go.Figure()
    for prefix, label, color in (
        ("baseline", "Baseline", "#5ee7ff"),
        ("candidate", "Candidate", "#ffcf5c"),
    ):
        safety.add_trace(
            go.Scatter(
                x=aligned["comparison_axis"],
                y=aligned[f"{prefix}_keep_out_margin_m"],
                name=f"{label} keep-out",
                line={"color": color},
            )
        )
        safety.add_trace(
            go.Scatter(
                x=aligned["comparison_axis"],
                y=aligned[f"{prefix}_corridor_margin_m"],
                name=f"{label} corridor",
                line={"color": color, "dash": "dot"},
            )
        )
    safety.add_hline(y=0, line_color="#ff496c", line_width=2)
    safety.update_layout(
        template="plotly_dark",
        title="Aligned safety margins",
        xaxis_title=str(aligned["alignment_label"].iloc[0]),
        uirevision=f"compare-safety-{result.spec.alignment}-{result.baseline_manifest.run_id}-{result.candidate_manifest.run_id}",
    )
    return trajectory, timeline, safety


def _comparison_kpis(html: Any, result: ComparisonResult) -> list[Any]:
    kpis = result.kpi_deltas
    items = (
        ("Closest approach Δ", f"{float(kpis['delta_closest_approach_m']):+.3f} m"),
        ("Closing rate Δ", f"{float(kpis['delta_final_closing_rate_m_s']):+.3f} m/s"),
        ("Propellant Δ", f"{float(kpis['delta_propellant_used_kg']):+.2f} kg"),
        ("Duration Δ", f"{float(kpis['delta_duration_s']):+.1f} s"),
        ("Safety violations Δ", f"{int(kpis['delta_safety_violations']):+d}"),
        ("Command changes Δ", f"{int(kpis['delta_command_changes']):+d}"),
    )
    return [
        html.Div([html.Span(label), html.Strong(value)], className="kpi-card")
        for label, value in items
    ]


def _comparison_detail_table(html: Any, result: ComparisonResult) -> Any:
    headers = ("Category", "Item", "Baseline", "Candidate", "Delta")
    rows = [
        html.Tr(
            [
                html.Td(str(row["category"])),
                html.Td(str(row["item"])),
                html.Td(str(int(row["baseline_count"]))),
                html.Td(str(int(row["candidate_count"]))),
                html.Td(f"{int(row['delta']):+d}"),
            ]
        )
        for _, row in result.detail_diffs.iterrows()
    ]
    return html.Table(
        [html.Thead(html.Tr([html.Th(item) for item in headers])), html.Tbody(rows)],
        className="outlier-table",
    )


def create_app(
    store: ReplayStore,
    live_scenarios: list[dict[str, str]] | None = None,
    risk_store: RiskStore | None = None,
    risk_materializer: RiskSampleMaterializer | None = None,
    application_state: ApplicationStateStore | None = None,
) -> Any:
    try:
        import plotly.graph_objects as go  # type: ignore[import-untyped]
        from dash import Dash, Input, Output, State, dcc, html
    except ImportError as exc:
        raise RuntimeError("The replay application requires the 'ui' optional dependency") from exc

    runs = store.list_runs()
    app = Dash(__name__, assets_folder=str(Path(__file__).with_name("assets")))
    app.title = "A3 DockLab Mission Replay"
    if risk_materializer is not None:
        from flask import redirect, request

        @app.server.get("/api/risk/materialize")
        def materialize_risk_sample() -> Any:
            ensemble_id = request.args.get("ensemble_id", "")
            try:
                sample_index = int(request.args.get("sample_index", ""))
                status = risk_materializer.materialize_sample(ensemble_id, sample_index)
            except (KeyError, ValueError) as exc:
                return {"error": str(exc)}, 400
            if status.state == "completed" and status.run_id:
                return redirect(f"/?workspace=replay&run_id={quote(status.run_id)}")
            return redirect(
                f"/?workspace=risk&materialization_operation={quote(status.operation_id)}"
            )

        @app.server.get("/api/risk/materialization-status")
        def risk_materialization_status() -> Any:
            operation_id = request.args.get("operation_id", "")
            if not operation_id:
                return {"error": "operation_id is required"}, 400
            status = risk_materializer.materialization_status(operation_id)
            return {
                "operation_id": status.operation_id,
                "state": status.state,
                "run_id": status.run_id,
                "detail": status.detail,
            }

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
    ensembles = risk_store.list_ensembles() if risk_store is not None else []
    risk_layout = html.Div(
        [
            html.H2("Risk Workspace"),
            html.P("Explore bounded Monte Carlo evidence with immutable ensemble provenance."),
            dcc.Interval(id="risk-materialization-poll", interval=2000, disabled=False),
            html.Div(id="risk-materialization-status", className="provenance-card"),
            dcc.Dropdown(
                id="risk-ensemble-selector",
                options=[
                    {
                        "label": f"{item.scenario} · {item.sample_count:,} samples · {item.ensemble_id}",
                        "value": item.ensemble_id,
                    }
                    for item in ensembles
                ],
                value=ensembles[0].ensemble_id if ensembles else None,
                placeholder="No ensembles available — run the Monte Carlo job first",
            ),
            html.Div(id="risk-kpis", className="kpi-strip risk-kpis"),
            html.Div(
                [dcc.Graph(id="risk-distributions"), dcc.Graph(id="risk-convergence")],
                className="risk-chart-grid",
            ),
            dcc.Graph(id="risk-sensitivity"),
            html.Div(
                [dcc.Graph(id="risk-contact"), dcc.Graph(id="risk-faults")],
                className="risk-chart-grid",
            ),
            html.H3("Highest-risk samples"),
            html.Div(id="risk-outliers"),
            html.Div(id="risk-provenance", className="provenance-card"),
        ]
    )
    run_options = [
        {
            "label": f"{run.scenario_id} · {run.run_id[-12:]} · schema {run.schema_version}",
            "value": run.run_id,
        }
        for run in runs
    ]
    compare_layout = html.Div(
        [
            html.H2("Compare Workspace"),
            html.P("Compare two immutable runs with explicit alignment and schema behavior."),
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Baseline"),
                            dcc.Dropdown(
                                id="compare-baseline",
                                options=run_options,
                                value=runs[0].run_id if runs else None,
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label("Candidate"),
                            dcc.Dropdown(
                                id="compare-candidate",
                                options=run_options,
                                value=runs[1].run_id
                                if len(runs) > 1
                                else runs[0].run_id
                                if runs
                                else None,
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label("Alignment"),
                            dcc.Dropdown(
                                id="compare-alignment",
                                options=[
                                    {"label": "Event time", "value": "event_time"},
                                    {"label": "Mission phase", "value": "mission_phase"},
                                ],
                                value="event_time",
                                clearable=False,
                            ),
                        ]
                    ),
                ],
                className="compare-controls",
            ),
            html.Label(
                "Schema mapping (candidate column → baseline column JSON; required when schemas differ)"
            ),
            dcc.Textarea(id="compare-schema-mapping", value="{}", className="schema-mapping"),
            html.Button("Compare runs", id="compare-submit", className="primary-control"),
            html.Div(
                [
                    dcc.Input(id="compare-save-name", placeholder="Comparison name"),
                    html.Button(
                        "Save comparison",
                        id="compare-save",
                        disabled=application_state is None,
                    ),
                    dcc.Dropdown(id="compare-saved", placeholder="Restore saved comparison"),
                ],
                className="compare-save-controls",
            ),
            html.Div(id="compare-save-status", className="muted"),
            html.Div(id="compare-error", className="comparison-error"),
            html.Div(id="compare-kpis", className="kpi-strip compare-kpis"),
            dcc.Graph(id="compare-trajectory"),
            html.Div(
                [dcc.Graph(id="compare-timeline"), dcc.Graph(id="compare-safety")],
                className="risk-chart-grid",
            ),
            html.H3("Command, policy, event, and safety differences"),
            html.Div(id="compare-details"),
            html.Div(id="compare-provenance", className="provenance-card"),
        ]
    )
    app.layout = html.Main(
        [
            dcc.Location(id="workspace-location", refresh="callback-nav"),
            dcc.Store(id="replay-data"),
            dcc.Store(id="client-clock"),
            dcc.Store(id="compare-spec"),
            html.Header(
                [html.H1("A3 DockLab"), html.Span("Interactive rendezvous & docking laboratory")]
            ),
            dcc.Tabs(
                id="workspace-tabs",
                value="live",
                children=[
                    dcc.Tab(label="Live Lab", value="live", children=live_layout),
                    dcc.Tab(label="Replay", value="replay", children=replay_layout),
                    dcc.Tab(label="Risk", value="risk", children=risk_layout),
                    dcc.Tab(label="Compare", value="compare", children=compare_layout),
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
        manifest = next(run for run in store.list_runs() if run.run_id == run_id)
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

    @app.callback(
        Output("risk-kpis", "children"),
        Output("risk-distributions", "figure"),
        Output("risk-convergence", "figure"),
        Output("risk-sensitivity", "figure"),
        Output("risk-contact", "figure"),
        Output("risk-faults", "figure"),
        Output("risk-outliers", "children"),
        Output("risk-provenance", "children"),
        Input("risk-ensemble-selector", "value"),
    )
    def configure_risk(ensemble_id: str | None) -> tuple[Any, ...]:
        if ensemble_id is None or risk_store is None:
            empty = go.Figure().update_layout(template="plotly_dark", uirevision="risk-empty")
            return [], empty, empty, empty, empty, empty, "", "No risk ensemble is available."
        budget = AnalysisBudget(max_rows=250_000, max_points_per_trace=10_000, timeout_s=30)
        result = risk_store.query_ensemble(ensemble_id, budget=budget)
        figures = _risk_figures(go, result, budget.max_points_per_trace)
        details = _risk_detail_figures(go, result)
        provenance = (
            f"Schema {result.ensemble.schema_version} · config "
            f"{result.ensemble.configuration_hash[:12]} · {result.rows_scanned:,} rows · "
            f"{result.query_latency_ms:.1f} ms · {result.ensemble.source_uri}"
        )
        return (
            _risk_kpis(html, result),
            *figures,
            *details,
            _outlier_table(
                html,
                result,
                {run.run_id for run in store.list_runs()},
                risk_materializer is not None,
            ),
            provenance,
        )

    @app.callback(
        Output("workspace-tabs", "value"),
        Output("run-selector", "value"),
        Output("run-selector", "options"),
        Input("workspace-location", "search"),
    )
    def restore_workspace_link(search: str | None) -> tuple[str, Any, list[dict[str, str]]]:
        query = parse_qs((search or "").lstrip("?"))
        workspace = query.get("workspace", ["live"])[0]
        if workspace not in {"live", "replay", "risk", "compare"}:
            workspace = "live"
        available_runs = store.list_runs()
        requested_run = query.get("run_id", [None])[0]
        valid_run = (
            requested_run if requested_run in {run.run_id for run in available_runs} else None
        )
        if workspace == "replay" and valid_run is None and available_runs:
            valid_run = available_runs[0].run_id
        options = [
            {"label": f"{run.scenario_id} · {run.run_id[-12:]}", "value": run.run_id}
            for run in available_runs
        ]
        return workspace, valid_run, options

    @app.callback(
        Output("risk-materialization-status", "children"),
        Output("risk-materialization-poll", "disabled"),
        Input("risk-materialization-poll", "n_intervals"),
        State("workspace-location", "search"),
    )
    def report_materialization_status(_: int, search: str | None) -> tuple[str, bool]:
        query = parse_qs((search or "").lstrip("?"))
        operation_id = query.get("materialization_operation", [None])[0]
        if operation_id is None or risk_materializer is None:
            return "", True
        status = risk_materializer.materialization_status(operation_id)
        terminal = status.state in {"completed", "failed", "unknown"}
        detail = f" · {status.detail}" if status.detail else ""
        return f"Sample materialization: {status.state}{detail}", terminal

    @app.callback(
        Output("compare-kpis", "children"),
        Output("compare-trajectory", "figure"),
        Output("compare-timeline", "figure"),
        Output("compare-safety", "figure"),
        Output("compare-provenance", "children"),
        Output("compare-error", "children"),
        Output("compare-details", "children"),
        Output("compare-spec", "data"),
        Input("compare-submit", "n_clicks"),
        State("compare-baseline", "value"),
        State("compare-candidate", "value"),
        State("compare-alignment", "value"),
        State("compare-schema-mapping", "value"),
    )
    def configure_comparison(
        _: int,
        baseline_id: str | None,
        candidate_id: str | None,
        alignment: str,
        mapping_json: str | None,
    ) -> tuple[Any, ...]:
        empty = go.Figure().update_layout(template="plotly_dark", uirevision="compare-empty")
        if baseline_id is None or candidate_id is None:
            return [], empty, empty, empty, "", "Select both runs.", "", None
        manifests = {run.run_id: run for run in store.list_runs()}
        try:
            result = compare_runs(
                store,
                manifests[baseline_id],
                manifests[candidate_id],
                alignment=alignment,
                schema_mapping=parse_schema_mapping(mapping_json),
                budget=AnalysisBudget(max_rows=250_000, max_points_per_trace=10_000, timeout_s=30),
            )
        except (KeyError, ValueError, TimeoutError) as exc:
            return [], empty, empty, empty, "", str(exc), "", None
        figures = _comparison_figures(go, result)
        provenance = (
            f"Baseline {result.baseline_manifest.run_id} ({result.baseline_manifest.configuration_hash[:12]}) · "
            f"candidate {result.candidate_manifest.run_id} ({result.candidate_manifest.configuration_hash[:12]}) · "
            f"{result.spec.alignment} · overlap {result.overlap_duration_s:.1f} s · "
            f"{result.rows_scanned:,} rows · {result.query_latency_ms:.1f} ms"
        )
        return (
            _comparison_kpis(html, result),
            *figures,
            provenance,
            "",
            _comparison_detail_table(html, result),
            result.spec.model_dump(mode="json"),
        )

    @app.callback(
        Output("compare-save-status", "children"),
        Output("compare-saved", "options"),
        Input("compare-save", "n_clicks"),
        State("compare-save-name", "value"),
        State("compare-spec", "data"),
        prevent_initial_call=True,
    )
    def save_comparison(
        _: int, name: str | None, spec_data: dict[str, Any] | None
    ) -> tuple[str, list[dict[str, str]]]:
        if application_state is None:
            return "Lakebase application state is unavailable.", []
        from flask import request

        owner = request.headers.get("X-Forwarded-Email", "local-operator")
        if not name or spec_data is None:
            return "Run a comparison and provide a name first.", []
        record = new_comparison(
            owner,
            name,
            str(spec_data["baseline"]["run_id"]),
            str(spec_data["candidate"]["run_id"]),
            str(spec_data["alignment"]),  # type: ignore[arg-type]
            json.dumps(spec_data, sort_keys=True, separators=(",", ":")),
        )
        application_state.save_comparison(record)
        options = [
            {"label": item.name, "value": item.comparison_id}
            for item in application_state.list_comparisons(owner)
        ]
        return f"Saved “{name}” with immutable artifact references.", options

    @app.callback(
        Output("compare-baseline", "value"),
        Output("compare-candidate", "value"),
        Output("compare-alignment", "value"),
        Output("compare-schema-mapping", "value"),
        Input("compare-saved", "value"),
        prevent_initial_call=True,
    )
    def restore_comparison(comparison_id: str | None) -> tuple[Any, ...]:
        if comparison_id is None or application_state is None:
            return None, None, "event_time", "{}"
        from flask import request

        owner = request.headers.get("X-Forwarded-Email", "local-operator")
        record = next(
            item
            for item in application_state.list_comparisons(owner)
            if item.comparison_id == comparison_id
        )
        spec = json.loads(record.comparison_spec_json)
        return (
            record.baseline_run_id,
            record.candidate_run_id,
            record.alignment,
            json.dumps(spec.get("schema_mapping", {}), indent=2, sort_keys=True),
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
