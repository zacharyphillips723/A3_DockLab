"""Reproducible run identity and provenance records."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from a3docklab.config import SimulationConfig


class AssumptionRecord(BaseModel):
    assumption_id: str
    area: str
    parameter: str
    default_value: str
    unit: str
    confidence: str
    rationale: str
    validation_path: str


class SourceRevision(BaseModel):
    source_date: date
    retrieved_date: date
    primary_source: str


class RunMetadata(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    created_at_utc: datetime
    scenario: str
    random_seed: int
    fidelity: str
    config_sha256: str
    source_revision: SourceRevision
    assumptions: list[AssumptionRecord] = Field(default_factory=list)


def canonical_config_json(config: SimulationConfig) -> str:
    """Serialize a validated configuration deterministically."""
    return json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def config_sha256(config: SimulationConfig) -> str:
    return hashlib.sha256(canonical_config_json(config).encode("utf-8")).hexdigest()


def deterministic_run_id(config: SimulationConfig) -> str:
    """Return an ID stable for the same scenario configuration and seed."""
    return f"{config.name}-{config_sha256(config)[:12]}"


def load_source_revision(path: str | Path) -> SourceRevision:
    with Path(path).open("r", encoding="utf-8") as stream:
        raw: dict[str, Any] = yaml.safe_load(stream)
    return SourceRevision.model_validate(raw)


def load_assumptions(path: str | Path) -> list[AssumptionRecord]:
    with Path(path).open("r", encoding="utf-8", newline="") as stream:
        return [AssumptionRecord.model_validate(row) for row in csv.DictReader(stream)]


def build_run_metadata(
    config: SimulationConfig,
    source_revision: SourceRevision,
    assumptions: list[AssumptionRecord],
    *,
    created_at_utc: datetime | None = None,
) -> RunMetadata:
    return RunMetadata(
        run_id=deterministic_run_id(config),
        created_at_utc=created_at_utc or datetime.now(UTC),
        scenario=config.name,
        random_seed=config.random_seed,
        fidelity=config.fidelity,
        config_sha256=config_sha256(config),
        source_revision=source_revision,
        assumptions=assumptions,
    )
