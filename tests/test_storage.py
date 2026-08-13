import json
from datetime import UTC, datetime
from pathlib import Path

from a3docklab.config import load_config
from a3docklab.run_metadata import build_run_metadata, load_assumptions, load_source_revision
from a3docklab.simulation.engine import run_cw
from a3docklab.telemetry.storage import LocalRunStorage


def test_local_storage_writes_portable_run_bundle(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    result = run_cw(config)
    metadata = build_run_metadata(
        config,
        load_source_revision(root / "docs/mission_facts.yaml"),
        load_assumptions(root / "docs/assumption_register.csv"),
        created_at_utc=datetime(2026, 8, 4, tzinfo=UTC),
    )

    LocalRunStorage(tmp_path).write_run(result.telemetry, metadata)

    assert (tmp_path / "telemetry.csv").is_file()
    stored = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert stored["run_id"] == metadata.run_id
