from datetime import UTC, datetime

import pandas as pd
import plotly.graph_objects as go
import pytest

from a3docklab.analysis.comparison import compare_runs, parse_schema_mapping
from a3docklab.analysis.contracts import AnalysisBudget
from a3docklab.telemetry.contracts import BundleManifest, StreamManifest
from a3docklab.visualization.dashboard import _comparison_figures


class ComparisonStore:
    def __init__(self) -> None:
        self.frames = {
            "baseline": self._frame(5, 0.0),
            "candidate": self._frame(3, 1.0),
        }

    @staticmethod
    def _frame(count: int, offset: float) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "event_time_ns": [index * 1_000_000_000 for index in range(count)],
                "x_m": [10.0 - index + offset for index in range(count)],
                "y_m": [offset] * count,
                "z_m": [0.0] * count,
                "range_m": [10.0 - index + offset for index in range(count)],
                "closing_rate_m_s": [0.2 + offset * 0.1] * count,
                "phase": ["approach"] * (count - 1) + ["final"],
                "propellant_used_kg": [float(index) + offset for index in range(count)],
                "keep_out_margin_m": [1.0] * count,
                "corridor_margin_m": [1.0] * (count - 1) + [-offset],
                "command_source": ["autopilot"] * count,
            }
        )

    def list_runs(self) -> list[BundleManifest]:
        return []

    def query_stream(self, run_id: str, stream: str, **_: object) -> pd.DataFrame:
        assert stream == "truth"
        return self.frames[run_id].copy()


def _manifest(run_id: str, schema: str = "3.0") -> BundleManifest:
    return BundleManifest(
        schema_version=schema,
        run_id=run_id,
        scenario_id="scenario",
        created_at_utc=datetime(2026, 8, 27, tzinfo=UTC),
        configuration_hash=f"hash-{run_id}",
        terminal_phase="final",
        streams=[
            StreamManifest(
                name="truth",
                path=f"bundles/{run_id}/truth.parquet",
                row_count=5,
                format="parquet",
                time_column="event_time_ns",
            )
        ],
        feature_allowlist_path="",
    )


def test_event_time_comparison_reports_overlap_and_kpi_deltas() -> None:
    result = compare_runs(ComparisonStore(), _manifest("baseline"), _manifest("candidate"))

    assert result.overlap_duration_s == 2.0
    assert len(result.aligned) == 3
    assert result.kpi_deltas["delta_propellant_used_kg"] == -1.0
    assert result.kpi_deltas["delta_safety_violations"] == 1.0
    safety = result.detail_diffs[result.detail_diffs["category"] == "Safety"]
    assert safety["delta"].max() == 1
    figures = _comparison_figures(go, result)
    assert figures[0].layout.uirevision.startswith("compare-trajectory-")
    assert [trace.name for trace in figures[0].data] == ["Baseline", "Candidate", "Target"]


def test_phase_alignment_and_schema_mapping_are_explicit() -> None:
    store = ComparisonStore()
    with pytest.raises(ValueError, match="explicit compatibility mapping"):
        compare_runs(store, _manifest("baseline", "2.0"), _manifest("candidate", "3.0"))

    result = compare_runs(
        store,
        _manifest("baseline", "2.0"),
        _manifest("candidate", "3.0"),
        alignment="mission_phase",
        schema_mapping={"range_m": "range_m"},
    )
    assert result.spec.alignment == "mission_phase"
    assert result.aligned["alignment_label"].iat[0] == "mission phase progress"


def test_comparison_rejects_invalid_mapping_and_row_budget() -> None:
    assert parse_schema_mapping('{"candidate_range": "range_m"}') == {"candidate_range": "range_m"}
    with pytest.raises(ValueError, match="JSON object"):
        parse_schema_mapping("[]")
    with pytest.raises(ValueError, match="budget permits"):
        compare_runs(
            ComparisonStore(),
            _manifest("baseline"),
            _manifest("candidate"),
            budget=AnalysisBudget(max_rows=7),
        )
