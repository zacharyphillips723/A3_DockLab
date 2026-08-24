import pytest
from pydantic import ValidationError

from a3docklab.analysis.contracts import (
    AnalysisArtifactRef,
    AnalysisBudget,
    ComparisonSpec,
    ReproducibleReviewRef,
)


def _artifact(run_id: str, schema_version: str = "3.0") -> AnalysisArtifactRef:
    return AnalysisArtifactRef(
        run_id=run_id,
        schema_version=schema_version,
        configuration_hash=f"hash-{run_id}",
        source_uri=f"delta://a3docklab_runs/{run_id}",
    )


def test_comparison_accepts_matching_schemas_and_bounded_budget() -> None:
    spec = ComparisonSpec(baseline=_artifact("run-a"), candidate=_artifact("run-b"))

    assert spec.alignment == "event_time"
    assert spec.budget.max_rows == 250_000


def test_comparison_requires_mapping_for_mismatched_schemas() -> None:
    with pytest.raises(ValidationError, match="explicit compatibility mapping"):
        ComparisonSpec(baseline=_artifact("run-a", "2.0"), candidate=_artifact("run-b", "3.0"))

    spec = ComparisonSpec(
        baseline=_artifact("run-a", "2.0"),
        candidate=_artifact("run-b", "3.0"),
        schema_mapping={"range": "range_m"},
    )
    assert spec.schema_mapping == {"range": "range_m"}


def test_analysis_budget_rejects_unbounded_requests() -> None:
    with pytest.raises(ValidationError):
        AnalysisBudget(max_rows=1_000_001)
    with pytest.raises(ValidationError):
        AnalysisBudget(timeout_s=121)


def test_review_reference_requires_reproducible_artifact_identity() -> None:
    review = ReproducibleReviewRef(
        review_event_id="review-1",
        reviewer="reviewer@example.com",
        artifacts=(_artifact("run-a"),),
    )
    assert review.artifacts[0].configuration_hash == "hash-run-a"
