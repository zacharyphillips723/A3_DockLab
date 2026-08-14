"""Command-line interface for starter simulations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import numpy as np
import typer

from a3docklab.config import load_config
from a3docklab.dynamics.combined_vehicle import applied_wrench_response, docked_stack_properties
from a3docklab.run_metadata import build_run_metadata, load_assumptions, load_source_revision
from a3docklab.simulation.engine import run_controlled, summarize
from a3docklab.simulation.monte_carlo import (
    load_monte_carlo_config,
    run_ensemble,
    write_ensemble,
)
from a3docklab.simulation.physics_comparison import compare_cw_and_two_body, validity_envelope
from a3docklab.telemetry.bundle import write_phase3_bundle
from a3docklab.telemetry.contracts import (
    load_fault_config,
    load_telemetry_config,
    phase3_identity,
)
from a3docklab.telemetry.generator import generate_streams
from a3docklab.telemetry.storage import LocalRunStorage
from a3docklab.visualization.dashboard import create_app
from a3docklab.visualization.replay import LocalReplayStore

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def simulate(
    config_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("runs"),
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
) -> None:
    """Run a controlled rendezvous and write a reproducible run bundle."""
    config = load_config(config_path)
    if config.fidelity != "cw":
        raise typer.BadParameter("The starter CLI currently executes fidelity='cw' only")
    result = run_controlled(config)
    metadata = build_run_metadata(
        config,
        load_source_revision(project_root / "docs/mission_facts.yaml"),
        load_assumptions(project_root / "docs/assumption_register.csv"),
    )
    run_directory = output / metadata.run_id
    LocalRunStorage(run_directory).write_run(result.telemetry, metadata, result.events)
    summary = summarize(result)
    typer.echo(f"Wrote {len(result.telemetry)} samples to {run_directory}")
    typer.echo(
        f"Terminal phase: {summary.terminal_phase}; elapsed: {summary.elapsed_time_s:.0f} s; "
        f"propellant: {summary.propellant_used_kg:.3f} kg; "
        f"closest approach: {summary.closest_approach_m:.3f} m; "
        f"warnings: {summary.warning_count}"
    )


@app.command()
def telemetry(
    config_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    telemetry_config_path: Annotated[Path, typer.Option("--telemetry-config")] = Path(
        "configs/telemetry/default.yaml"
    ),
    faults_path: Annotated[Path | None, typer.Option("--faults")] = None,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("bundles"),
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
) -> None:
    """Generate a versioned Phase 3 multi-rate telemetry bundle."""
    config = load_config(config_path)
    result = run_controlled(config)
    metadata = build_run_metadata(
        config,
        load_source_revision(project_root / "docs/mission_facts.yaml"),
        load_assumptions(project_root / "docs/assumption_register.csv"),
    )
    telemetry_config = load_telemetry_config(telemetry_config_path)
    fault_config = load_fault_config(faults_path)
    run_id, phase3_hash = phase3_identity(
        config.name, metadata.config_sha256, telemetry_config, fault_config
    )
    metadata = metadata.model_copy(update={"run_id": run_id, "config_sha256": phase3_hash})
    streams = generate_streams(
        result,
        telemetry_config,
        fault_config,
        random_seed=config.random_seed,
        run_id=run_id,
    )
    run_directory = output / metadata.run_id
    summary = summarize(result)
    write_phase3_bundle(run_directory, streams, metadata, summary.terminal_phase)
    typer.echo(
        f"Wrote Phase 3 bundle to {run_directory}: {len(streams.truth)} truth, "
        f"{len(streams.navigation)} navigation, {len(streams.events)} events"
    )


@app.command()
def replay(
    bundles: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8050,
) -> None:
    """Serve the local mission replay application."""
    dashboard = create_app(LocalReplayStore(bundles))
    dashboard.run(host=host, port=port, debug=False)


@app.command("physics-report")
def physics_report(
    config_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("reports/physics"),
) -> None:
    """Generate CW numerical and nonlinear two-body comparison artifacts."""
    config = load_config(config_path)
    comparison, summary = compare_cw_and_two_body(
        config, duration_s=min(config.duration_s, 3600.0), step_s=10.0
    )
    envelope = validity_envelope(
        config,
        separations_m=(100.0, 1_000.0, 10_000.0),
        durations_s=(600.0, 1_800.0, 3_600.0),
        step_s=20.0,
    )
    output.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(output / "trajectory_comparison.csv", index=False)
    envelope.to_csv(output / "validity_envelope.csv", index=False)
    (output / "summary.json").write_text(json.dumps(summary.__dict__, indent=2), encoding="utf-8")
    typer.echo(
        f"Wrote physics report to {output}; max numerical CW error "
        f"{summary.maximum_cw_numerical_error_m:.3e} m; max nonlinear difference "
        f"{summary.maximum_two_body_position_error_m:.3f} m"
    )


@app.command("stack-report")
def stack_report(
    config_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("reports/stack"),
) -> None:
    """Generate combined docked-stack mass-property and coupling artifacts."""
    config = load_config(config_path)
    stack = docked_stack_properties(config)
    unit_axis_responses: dict[str, dict[str, list[float]]] = {}
    for index, axis in enumerate(("x", "y", "z")):
        force = np.zeros(3)
        force[index] = 1.0
        response = applied_wrench_response(
            stack, force, np.zeros(3), stack.chaser_center_reference_m
        )
        unit_axis_responses[axis] = {
            "induced_torque_n_m_per_n": response.induced_torque_reference_n_m.tolist(),
            "angular_acceleration_rad_s2_per_n": (
                response.angular_acceleration_reference_rad_s2.tolist()
            ),
        }
    payload = {
        "scenario": config.name,
        "geometry": config.docking.geometry,
        "total_mass_kg": stack.total_mass_kg,
        "center_of_mass_target_frame_m": stack.center_of_mass_reference_m.tolist(),
        "inertia_about_com_target_frame_kg_m2": (stack.inertia_about_com_reference_kg_m2.tolist()),
        "principal_moments_kg_m2": stack.principal_moments_kg_m2.tolist(),
        "chaser_center_target_frame_m": stack.chaser_center_reference_m.tolist(),
        "target_center_target_frame_m": stack.target_center_reference_m.tolist(),
        "docking_interface_target_frame_m": stack.docking_interface_reference_m.tolist(),
        "unit_force_at_chaser_center": unit_axis_responses,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "combined_stack.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    typer.echo(
        f"Wrote combined-stack report to {output}; mass {stack.total_mass_kg:.1f} kg; "
        f"COM {stack.center_of_mass_reference_m.tolist()} m in target frame"
    )


@app.command("monte-carlo")
def monte_carlo(
    config_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    ensemble_config_path: Annotated[
        Path, typer.Option("--ensemble-config", exists=True, readable=True)
    ] = Path("configs/monte_carlo/default.yaml"),
    telemetry_config_path: Annotated[Path, typer.Option("--telemetry-config")] = Path(
        "configs/telemetry/default.yaml"
    ),
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("ensembles"),
) -> None:
    """Run a deterministic local ensemble and write risk/convergence artifacts."""
    scenario = load_config(config_path)
    ensemble_config = load_monte_carlo_config(ensemble_config_path)
    telemetry_config = load_telemetry_config(telemetry_config_path)
    result = run_ensemble(scenario, ensemble_config, telemetry_config)
    directory = output / str(result.manifest["ensemble_id"])
    write_ensemble(directory, result)
    typer.echo(
        f"Wrote {len(result.runs)} Monte Carlo runs to {directory}; "
        f"capture rate {result.risk_summary['capture_rate']:.3f}; "
        f"abort rate {result.risk_summary['abort_rate']:.3f}"
    )


if __name__ == "__main__":
    app()
