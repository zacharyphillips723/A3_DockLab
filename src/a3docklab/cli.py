"""Command-line interface for starter simulations."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from a3docklab.config import load_config
from a3docklab.run_metadata import build_run_metadata, load_assumptions, load_source_revision
from a3docklab.simulation.engine import run_cw
from a3docklab.telemetry.storage import LocalRunStorage

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def simulate(
    config_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("runs"),
    project_root: Annotated[Path, typer.Option("--project-root")] = Path("."),
) -> None:
    """Run a configured reference simulation and write a reproducible run bundle."""
    config = load_config(config_path)
    if config.fidelity != "cw":
        raise typer.BadParameter("The starter CLI currently executes fidelity='cw' only")
    result = run_cw(config)
    metadata = build_run_metadata(
        config,
        load_source_revision(project_root / "docs/mission_facts.yaml"),
        load_assumptions(project_root / "docs/assumption_register.csv"),
    )
    run_directory = output / metadata.run_id
    LocalRunStorage(run_directory).write_run(result.telemetry, metadata)
    typer.echo(f"Wrote {len(result.telemetry)} samples to {run_directory}")


if __name__ == "__main__":
    app()
