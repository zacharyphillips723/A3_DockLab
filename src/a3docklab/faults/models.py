"""Composable fault-injection definitions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FaultWindow(BaseModel):
    name: Literal[
        "navigation_drift",
        "thruster_underperformance",
        "communications_latency",
        "attitude_oscillation",
        "unexpected_closing_rate",
        "docking_alignment_error",
        "telemetry_channel_loss",
    ]
    start_s: float = Field(ge=0.0)
    duration_s: float = Field(gt=0.0)
    severity: float = Field(gt=0.0)
    affected_channels: tuple[str, ...] = ()
