"""Bounded, reproducible run comparison over the ReplayStore contract."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from a3docklab.analysis.contracts import AnalysisArtifactRef, AnalysisBudget, ComparisonSpec
from a3docklab.telemetry.contracts import BundleManifest
from a3docklab.visualization.replay import ReplayStore


@dataclass(frozen=True)
class ComparisonResult:
    spec: ComparisonSpec
    baseline_manifest: BundleManifest
    candidate_manifest: BundleManifest
    baseline: pd.DataFrame
    candidate: pd.DataFrame
    aligned: pd.DataFrame
    kpi_deltas: dict[str, float | int | str]
    query_latency_ms: float
    rows_scanned: int
    overlap_duration_s: float


def _artifact(manifest: BundleManifest) -> AnalysisArtifactRef:
    truth = next(stream for stream in manifest.streams if stream.name == "truth")
    return AnalysisArtifactRef(
        run_id=manifest.run_id,
        schema_version=manifest.schema_version,
        configuration_hash=manifest.configuration_hash,
        source_uri=truth.path,
    )


def _time_s(frame: pd.DataFrame) -> np.ndarray:
    return frame["event_time_ns"].to_numpy(dtype=np.float64) / 1_000_000_000


def _phase_coordinate(frame: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    phases = frame["phase"].astype(str)
    order = list(dict.fromkeys(phases.tolist()))
    coordinate = np.zeros(len(frame), dtype=float)
    for index, phase in enumerate(order):
        members = np.flatnonzero(phases.to_numpy() == phase)
        coordinate[members] = index + np.linspace(0.0, 1.0, len(members), endpoint=False)
    return coordinate, order


def _align_event_time(
    baseline: pd.DataFrame, candidate: pd.DataFrame
) -> tuple[pd.DataFrame, float]:
    baseline_time = _time_s(baseline)
    candidate_time = _time_s(candidate)
    start = max(float(baseline_time[0]), float(candidate_time[0]))
    end = min(float(baseline_time[-1]), float(candidate_time[-1]))
    if end <= start:
        raise ValueError("runs have no overlapping event-time interval")
    count = min(len(baseline), len(candidate))
    axis = np.linspace(start, end, count)
    aligned: dict[str, Any] = {"comparison_axis": axis, "alignment_label": "event time (s)"}
    for name, frame, times in (
        ("baseline", baseline, baseline_time),
        ("candidate", candidate, candidate_time),
    ):
        for column in (
            "range_m",
            "closing_rate_m_s",
            "propellant_used_kg",
            "keep_out_margin_m",
            "corridor_margin_m",
        ):
            if column in frame:
                aligned[f"{name}_{column}"] = np.interp(axis, times, frame[column].astype(float))
    return pd.DataFrame(aligned), end - start


def _align_phase(baseline: pd.DataFrame, candidate: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    baseline_axis, baseline_phases = _phase_coordinate(baseline)
    candidate_axis, candidate_phases = _phase_coordinate(candidate)
    common = [phase for phase in baseline_phases if phase in candidate_phases]
    if not common:
        raise ValueError("runs share no mission phases")
    end = float(len(common))
    count = min(len(baseline), len(candidate))
    axis = np.linspace(0.0, end, count, endpoint=False)
    aligned: dict[str, Any] = {"comparison_axis": axis, "alignment_label": "mission phase progress"}
    for name, frame, source_axis, phases in (
        ("baseline", baseline, baseline_axis, baseline_phases),
        ("candidate", candidate, candidate_axis, candidate_phases),
    ):
        phase_indexes = [phases.index(phase) for phase in common]
        keep = np.isin(np.floor(source_axis).astype(int), phase_indexes)
        compact_axis = np.zeros(int(keep.sum()), dtype=float)
        kept_source = source_axis[keep]
        for new_index, old_index in enumerate(phase_indexes):
            phase_members = np.floor(kept_source).astype(int) == old_index
            compact_axis[phase_members] = new_index + (kept_source[phase_members] - old_index)
        for column in (
            "range_m",
            "closing_rate_m_s",
            "propellant_used_kg",
            "keep_out_margin_m",
            "corridor_margin_m",
        ):
            if column in frame:
                aligned[f"{name}_{column}"] = np.interp(
                    axis, compact_axis, frame.loc[keep, column].astype(float)
                )
    return pd.DataFrame(aligned), min(float(_time_s(baseline)[-1]), float(_time_s(candidate)[-1]))


def _kpis(baseline: pd.DataFrame, candidate: pd.DataFrame) -> dict[str, float | int | str]:
    def metric(frame: pd.DataFrame) -> dict[str, float | int | str]:
        return {
            "duration_s": float(_time_s(frame)[-1] - _time_s(frame)[0]),
            "closest_approach_m": float(frame["range_m"].min()),
            "final_closing_rate_m_s": float(frame["closing_rate_m_s"].iloc[-1]),
            "propellant_used_kg": float(frame["propellant_used_kg"].iloc[-1]),
            "safety_violations": int(
                ((frame["keep_out_margin_m"] < 0) | (frame["corridor_margin_m"] < 0)).sum()
            ),
            "command_changes": int(
                frame["command_source"]
                .astype(str)
                .ne(frame["command_source"].astype(str).shift())
                .sum()
            )
            if "command_source" in frame
            else 0,
            "terminal_phase": str(frame["phase"].iloc[-1]),
        }

    left, right = metric(baseline), metric(candidate)
    output: dict[str, float | int | str] = {
        "baseline_terminal_phase": left["terminal_phase"],
        "candidate_terminal_phase": right["terminal_phase"],
    }
    for key in (
        "duration_s",
        "closest_approach_m",
        "final_closing_rate_m_s",
        "propellant_used_kg",
        "safety_violations",
        "command_changes",
    ):
        output[f"baseline_{key}"] = left[key]
        output[f"candidate_{key}"] = right[key]
        output[f"delta_{key}"] = float(right[key]) - float(left[key])
    return output


def compare_runs(
    store: ReplayStore,
    baseline_manifest: BundleManifest,
    candidate_manifest: BundleManifest,
    *,
    alignment: str = "event_time",
    schema_mapping: dict[str, str] | None = None,
    budget: AnalysisBudget | None = None,
) -> ComparisonResult:
    """Load, validate, align, and summarize two immutable run artifacts."""
    started = time.perf_counter()
    budget = budget or AnalysisBudget()
    spec = ComparisonSpec(
        baseline=_artifact(baseline_manifest),
        candidate=_artifact(candidate_manifest),
        alignment=alignment,
        schema_mapping=schema_mapping or {},
        budget=budget,
    )
    max_points = budget.max_points_per_trace
    baseline = store.query_stream(baseline_manifest.run_id, "truth", max_points=max_points)
    candidate = store.query_stream(candidate_manifest.run_id, "truth", max_points=max_points)
    rows_scanned = len(baseline) + len(candidate)
    if rows_scanned > budget.max_rows:
        raise ValueError(
            f"comparison requires {rows_scanned:,} rows; budget permits {budget.max_rows:,}"
        )
    if spec.schema_mapping:
        candidate = candidate.rename(columns=spec.schema_mapping)
    required = {
        "event_time_ns",
        "x_m",
        "y_m",
        "z_m",
        "range_m",
        "closing_rate_m_s",
        "phase",
        "propellant_used_kg",
        "keep_out_margin_m",
        "corridor_margin_m",
    }
    missing = required - set(baseline.columns) | required - set(candidate.columns)
    if missing:
        raise ValueError(f"comparison channels are missing: {', '.join(sorted(missing))}")
    aligned, overlap = (
        _align_event_time(baseline, candidate)
        if alignment == "event_time"
        else _align_phase(baseline, candidate)
    )
    elapsed = time.perf_counter() - started
    if elapsed > budget.timeout_s:
        raise TimeoutError(f"comparison exceeded its {budget.timeout_s:.1f}s budget")
    return ComparisonResult(
        spec,
        baseline_manifest,
        candidate_manifest,
        baseline,
        candidate,
        aligned,
        _kpis(baseline, candidate),
        elapsed * 1000,
        rows_scanned,
        overlap,
    )


def parse_schema_mapping(value: str | None) -> dict[str, str]:
    if not value or not value.strip():
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in parsed.items()
    ):
        raise ValueError(
            "schema mapping must be a JSON object of candidate-to-baseline column names"
        )
    return parsed
