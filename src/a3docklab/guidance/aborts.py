"""Abort decisions produced by deterministic safety rules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AbortMode(StrEnum):
    BRAKING = "braking"
    RETREAT = "retreat"


@dataclass(frozen=True)
class AbortDecision:
    mode: AbortMode
    reason: str
