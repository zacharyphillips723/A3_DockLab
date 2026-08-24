"""Versioned analysis contracts shared by workspace UX and operational controls."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AnalysisArtifactRef(BaseModel):
    run_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    configuration_hash: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)


class AnalysisBudget(BaseModel):
    """Hard query limits applied before risk/comparison work is scheduled."""

    max_rows: int = Field(default=250_000, ge=1, le=1_000_000)
    max_points_per_trace: int = Field(default=10_000, ge=100, le=100_000)
    timeout_s: float = Field(default=30.0, gt=0.0, le=120.0)


class ComparisonSpec(BaseModel):
    schema_version: str = "1.0"
    baseline: AnalysisArtifactRef
    candidate: AnalysisArtifactRef
    alignment: Literal["event_time", "mission_phase"] = "event_time"
    schema_mapping: dict[str, str] = Field(default_factory=dict)
    budget: AnalysisBudget = Field(default_factory=AnalysisBudget)

    @model_validator(mode="after")
    def require_explicit_schema_compatibility(self) -> ComparisonSpec:
        if (
            self.baseline.schema_version != self.candidate.schema_version
            and not self.schema_mapping
        ):
            raise ValueError(
                "mismatched artifact schemas require an explicit compatibility mapping"
            )
        return self


class ReproducibleReviewRef(BaseModel):
    schema_version: str = "1.0"
    review_event_id: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    artifacts: tuple[AnalysisArtifactRef, ...] = Field(min_length=1)
    view_state_json: str = "{}"
