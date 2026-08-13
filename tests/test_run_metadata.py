from datetime import UTC, datetime
from pathlib import Path

from a3docklab.config import load_config
from a3docklab.run_metadata import (
    build_run_metadata,
    config_sha256,
    deterministic_run_id,
    load_assumptions,
    load_source_revision,
)


def test_run_identity_is_deterministic_and_provenance_is_loaded() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/scenarios/blue_moon_side.yaml")
    source = load_source_revision(root / "docs/mission_facts.yaml")
    assumptions = load_assumptions(root / "docs/assumption_register.csv")

    metadata = build_run_metadata(
        config,
        source,
        assumptions,
        created_at_utc=datetime(2026, 8, 4, tzinfo=UTC),
    )

    assert metadata.run_id == deterministic_run_id(config)
    assert metadata.config_sha256 == config_sha256(config)
    assert metadata.source_revision.source_date.isoformat() == "2026-07-15"
    assert {item.assumption_id for item in metadata.assumptions} >= {"A-001", "A-008"}
