"""Storage-neutral Monte Carlo risk queries and bounded display payloads."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from a3docklab.analysis.contracts import AnalysisBudget
from a3docklab.config import SimulationConfig
from a3docklab.platform.delta import DeltaCatalog, TableFilter
from a3docklab.run_metadata import build_run_metadata, load_assumptions, load_source_revision
from a3docklab.simulation.engine import run_controlled, summarize
from a3docklab.simulation.monte_carlo import MonteCarloConfig, _apply_sample, _fault_config
from a3docklab.telemetry.bundle import write_phase3_bundle
from a3docklab.telemetry.contracts import TelemetryConfig, phase3_identity
from a3docklab.telemetry.generator import generate_streams


@dataclass(frozen=True)
class RiskEnsemble:
    ensemble_id: str
    scenario: str
    schema_version: str
    configuration_hash: str
    source_uri: str
    sample_count: int


@dataclass(frozen=True)
class RiskQueryResult:
    ensemble: RiskEnsemble
    summary: dict[str, float | int]
    runs: pd.DataFrame
    convergence: pd.DataFrame
    sensitivity: pd.DataFrame
    fault_sensitivity: pd.DataFrame
    query_latency_ms: float
    rows_scanned: int


class RiskStore(Protocol):
    def list_ensembles(self) -> list[RiskEnsemble]: ...

    def query_ensemble(
        self, ensemble_id: str, *, budget: AnalysisBudget | None = None
    ) -> RiskQueryResult: ...


class RiskSampleMaterializer(Protocol):
    def materialize_sample(self, ensemble_id: str, sample_index: int) -> MaterializationStatus: ...

    def materialization_status(self, operation_id: str) -> MaterializationStatus: ...


@dataclass(frozen=True)
class MaterializationStatus:
    operation_id: str
    state: str
    run_id: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class RebuiltRiskSample:
    run_id: str
    streams: Any
    metadata: Any
    terminal_phase: str


def rebuild_risk_sample(
    manifest: dict[str, Any], row: pd.Series, project_root: str | Path
) -> RebuiltRiskSample:
    """Rebuild one sample solely from immutable ensemble inputs."""
    if "ensemble_config" not in manifest or "base_configuration" not in manifest:
        raise ValueError("this older ensemble lacks the configuration required to rebuild a sample")
    config = MonteCarloConfig.model_validate(manifest["ensemble_config"])
    scenario = _apply_sample(SimulationConfig.model_validate(manifest["base_configuration"]), row)
    sampled_faults = json.loads(str(row["sampled_faults"]))
    handoff_names = {
        fault.name for fault in config.faults
        if fault.name not in {"navigation_drift", "thruster_underperformance", "communications_latency"}
    }
    scenario.handoff.injected_fault = next(
        (name for name in sampled_faults if name in handoff_names), "none"
    )  # type: ignore[assignment]
    result = run_controlled(scenario)
    telemetry_payload = manifest.get("telemetry_configuration")
    telemetry_config = (
        TelemetryConfig.model_validate(telemetry_payload)
        if telemetry_payload is not None
        else TelemetryConfig()
    )
    faults = _fault_config(config, sampled_faults, scenario.duration_s)
    root = Path(project_root)
    metadata = build_run_metadata(
        scenario,
        load_source_revision(root / "docs/mission_facts.yaml"),
        load_assumptions(root / "docs/assumption_register.csv"),
    )
    run_id, artifact_hash = phase3_identity(
        scenario.name, metadata.config_sha256, telemetry_config, faults
    )
    metadata = metadata.model_copy(update={"run_id": run_id, "config_sha256": artifact_hash})
    streams = generate_streams(
        result, telemetry_config, faults, random_seed=scenario.random_seed, run_id=run_id
    )
    return RebuiltRiskSample(run_id, streams, metadata, summarize(result).terminal_phase)


class LocalRiskSampleMaterializer:
    """Deterministically rebuild one ensemble sample as a Phase 3 replay bundle."""

    def __init__(
        self,
        ensemble_root: str | Path,
        bundle_root: str | Path,
        project_root: str | Path,
    ) -> None:
        self.ensemble_root = Path(ensemble_root)
        self.bundle_root = Path(bundle_root)
        self.project_root = Path(project_root)

    def materialize_sample(self, ensemble_id: str, sample_index: int) -> MaterializationStatus:
        available = {item.ensemble_id for item in LocalRiskStore(self.ensemble_root).list_ensembles()}
        if ensemble_id not in available:
            raise KeyError(f"Unknown ensemble {ensemble_id!r}")
        directory = self.ensemble_root / ensemble_id
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        rows = pd.read_parquet(directory / "runs.parquet")
        selected = rows.loc[rows["sample_index"] == sample_index]
        if len(selected) != 1:
            raise KeyError(f"Unknown sample {sample_index!r} in ensemble {ensemble_id!r}")
        rebuilt = rebuild_risk_sample(manifest, selected.iloc[0], self.project_root)
        write_phase3_bundle(
            self.bundle_root / rebuilt.run_id,
            rebuilt.streams,
            rebuilt.metadata,
            rebuilt.terminal_phase,
        )
        return MaterializationStatus(rebuilt.run_id, "completed", rebuilt.run_id)

    def materialization_status(self, operation_id: str) -> MaterializationStatus:
        exists = (self.bundle_root / operation_id / "manifest.json").exists()
        return MaterializationStatus(
            operation_id, "completed" if exists else "unknown", operation_id if exists else None
        )


def _manifest(metadata: dict[str, Any], source_uri: str) -> RiskEnsemble:
    return RiskEnsemble(
        ensemble_id=str(metadata["ensemble_id"]),
        scenario=str(metadata.get("scenario", "unknown")),
        schema_version=str(metadata.get("schema_version", "1.0")),
        configuration_hash=str(
            metadata.get(
                "configuration_sha256", metadata.get("config_sha256", metadata.get("digest", "unknown"))
            )
        ),
        source_uri=source_uri,
        sample_count=int(metadata["sample_count"]),
    )


def _sensitivity(runs: pd.DataFrame) -> pd.DataFrame:
    """Rank sampled numeric inputs by association with capture and miss distance."""
    excluded = {
        "sample_index", "sample_seed", "capture_success", "abort", "elapsed_time_s",
        "propellant_used_kg", "closest_approach_m", "warning_count",
        "contact_closing_rate_m_s", "contact_lateral_offset_m",
        "contact_angular_error_deg", "capture_dissipated_energy_j",
        "maximum_lateral_offset_m", "maximum_angular_error_deg",
        "allocation_saturation_fraction", "handoff_rollback", "sampled_fault_count",
    }
    inputs = [
        column for column in runs.select_dtypes(include="number").columns if column not in excluded
    ]
    rows: list[dict[str, float | str]] = []
    capture = runs["capture_success"].astype(float)
    closest = runs["closest_approach_m"].astype(float)
    capture_varies = capture.nunique(dropna=True) > 1
    closest_varies = closest.nunique(dropna=True) > 1
    for column in inputs:
        values = runs[column].astype(float)
        if values.nunique(dropna=True) < 2:
            continue
        capture_correlation = float(values.corr(capture)) if capture_varies else 0.0
        miss_correlation = float(values.corr(closest)) if closest_varies else 0.0
        capture_correlation = capture_correlation if pd.notna(capture_correlation) else 0.0
        miss_correlation = miss_correlation if pd.notna(miss_correlation) else 0.0
        rows.append(
            {
                "parameter": column,
                "capture_correlation": capture_correlation,
                "miss_distance_correlation": miss_correlation,
                "importance": max(abs(capture_correlation), abs(miss_correlation)),
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=("parameter", "capture_correlation", "miss_distance_correlation", "importance")
        )
    return pd.DataFrame(rows).sort_values("importance", ascending=False).reset_index(drop=True)


def _fault_sensitivity(runs: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for _, run in runs.iterrows():
        faults = json.loads(str(run.get("sampled_faults", "[]"))) or ["No sampled fault"]
        for fault in faults:
            rows.append(
                {
                    "fault": fault,
                    "capture_success": bool(run["capture_success"]),
                    "abort": bool(run["abort"]),
                    "closest_approach_m": float(run["closest_approach_m"]),
                }
            )
    if not rows:
        return pd.DataFrame(columns=("fault", "run_count", "capture_rate", "abort_rate", "mean_closest_approach_m"))
    return (
        pd.DataFrame(rows)
        .groupby("fault", as_index=False)
        .agg(
            run_count=("capture_success", "size"),
            capture_rate=("capture_success", "mean"),
            abort_rate=("abort", "mean"),
            mean_closest_approach_m=("closest_approach_m", "mean"),
        )
        .sort_values(["abort_rate", "run_count"], ascending=[False, False])
        .reset_index(drop=True)
    )


def _result(
    ensemble: RiskEnsemble,
    summary: dict[str, float | int],
    runs: pd.DataFrame,
    convergence: pd.DataFrame,
    budget: AnalysisBudget,
    started: float,
) -> RiskQueryResult:
    rows_scanned = len(runs) + len(convergence)
    if rows_scanned > budget.max_rows:
        raise ValueError(
            f"risk query requires {rows_scanned:,} rows; budget permits {budget.max_rows:,}"
        )
    sensitivity = _sensitivity(runs)
    fault_sensitivity = _fault_sensitivity(runs)
    elapsed = time.perf_counter() - started
    if elapsed > budget.timeout_s:
        raise TimeoutError(f"risk query exceeded its {budget.timeout_s:.1f}s budget")
    return RiskQueryResult(
        ensemble, summary, runs, convergence, sensitivity, fault_sensitivity,
        elapsed * 1000, rows_scanned
    )


class LocalRiskStore:
    """Read immutable ensembles written by ``a3docklab monte-carlo``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def list_ensembles(self) -> list[RiskEnsemble]:
        ensembles = []
        for path in sorted(self.root.glob("*/manifest.json")):
            metadata = json.loads(path.read_text(encoding="utf-8"))
            ensembles.append(_manifest(metadata, path.parent.resolve().as_uri()))
        return ensembles

    def query_ensemble(
        self, ensemble_id: str, *, budget: AnalysisBudget | None = None
    ) -> RiskQueryResult:
        started = time.perf_counter()
        budget = budget or AnalysisBudget()
        directory = self.root / ensemble_id
        metadata = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        summary = json.loads((directory / "risk_summary.json").read_text(encoding="utf-8"))
        runs = pd.read_parquet(directory / "runs.parquet")
        convergence = pd.read_parquet(directory / "convergence.parquet")
        return _result(
            _manifest(metadata, directory.resolve().as_uri()),
            summary, runs, convergence, budget, started,
        )


class DeltaRiskStore:
    """RiskStore backed by Monte Carlo tables written by the Databricks Job."""

    def __init__(self, catalog: DeltaCatalog, table_prefix: str = "a3docklab") -> None:
        self.catalog = catalog
        self.table_prefix = table_prefix

    def _table(self, suffix: str) -> str:
        return f"{self.table_prefix}_{suffix}"

    def list_ensembles(self) -> list[RiskEnsemble]:
        frame = self.catalog.read_table(self._table("ensembles"))
        return [
            _manifest(json.loads(str(row["manifest_json"])), f"delta://{self._table('ensembles')}")
            for _, row in frame.iterrows()
        ]

    def query_ensemble(
        self, ensemble_id: str, *, budget: AnalysisBudget | None = None
    ) -> RiskQueryResult:
        started = time.perf_counter()
        budget = budget or AnalysisBudget()
        metadata_rows = self.catalog.read_table(
            self._table("ensembles"), filters=(TableFilter("ensemble_id", "eq", ensemble_id),)
        )
        if len(metadata_rows) != 1:
            raise KeyError(f"Unknown ensemble {ensemble_id!r}")
        row = metadata_rows.iloc[0]
        metadata = json.loads(str(row["manifest_json"]))
        runs = self.catalog.read_table(
            self._table("ensemble_runs"),
            filters=(TableFilter("ensemble_id", "eq", ensemble_id),),
        ).drop(columns="ensemble_id", errors="ignore")
        convergence = self.catalog.read_table(
            self._table("ensemble_convergence"),
            filters=(TableFilter("ensemble_id", "eq", ensemble_id),),
            order_by=("sample_count",),
        ).drop(columns="ensemble_id", errors="ignore")
        return _result(
            _manifest(metadata, f"delta://{self._table('ensembles')}"),
            json.loads(str(row["risk_summary_json"])),
            runs, convergence, budget, started,
        )
