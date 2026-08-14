"""Deterministic rendezvous phase state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from a3docklab.config import GuidanceConfig


class MissionPhase(StrEnum):
    INITIALIZATION = "initialization"
    FAR_FIELD_APPROACH = "far_field_approach"
    HOLD_POINT_1 = "hold_point_1"
    PROXIMITY_OPERATIONS = "proximity_operations"
    HOLD_POINT_2 = "hold_point_2"
    FINAL_APPROACH = "final_approach"
    SOFT_CAPTURE = "soft_capture"
    DOCKED_STACK_CONTROL = "docked_stack_control"
    COMPLETE = "complete"
    ABORT = "abort"


@dataclass
class PhaseMachine:
    guidance: GuidanceConfig
    phase: MissionPhase = MissionPhase.INITIALIZATION
    entered_at_s: float = 0.0

    def transition(self, phase: MissionPhase, time_s: float) -> bool:
        if phase == self.phase:
            return False
        self.phase = phase
        self.entered_at_s = time_s
        return True

    def advance(
        self,
        range_m: float,
        time_s: float,
        capture_distance_m: float,
        hold_speed_m_s: float = 0.0,
        capture_allowed: bool = True,
        post_capture_complete_allowed: bool = True,
        post_handoff_control_duration_s: float = 0.0,
    ) -> bool:
        outer, inner = self.guidance.hold_points_m
        if self.phase == MissionPhase.INITIALIZATION:
            return self.transition(MissionPhase.FAR_FIELD_APPROACH, time_s)
        if self.phase == MissionPhase.FAR_FIELD_APPROACH and range_m <= outer:
            return self.transition(MissionPhase.HOLD_POINT_1, time_s)
        if (
            self.phase == MissionPhase.HOLD_POINT_1
            and time_s - self.entered_at_s >= self.guidance.hold_duration_s
            and hold_speed_m_s <= 0.02
        ):
            return self.transition(MissionPhase.PROXIMITY_OPERATIONS, time_s)
        if self.phase == MissionPhase.PROXIMITY_OPERATIONS and range_m <= inner:
            return self.transition(MissionPhase.HOLD_POINT_2, time_s)
        if (
            self.phase == MissionPhase.HOLD_POINT_2
            and time_s - self.entered_at_s >= self.guidance.hold_duration_s
            and hold_speed_m_s <= 0.02
        ):
            return self.transition(MissionPhase.FINAL_APPROACH, time_s)
        if (
            self.phase == MissionPhase.FINAL_APPROACH
            and range_m <= capture_distance_m
            and capture_allowed
        ):
            return self.transition(MissionPhase.SOFT_CAPTURE, time_s)
        if self.phase == MissionPhase.SOFT_CAPTURE and post_capture_complete_allowed:
            return self.transition(MissionPhase.DOCKED_STACK_CONTROL, time_s)
        if (
            self.phase == MissionPhase.DOCKED_STACK_CONTROL
            and time_s - self.entered_at_s >= post_handoff_control_duration_s
        ):
            return self.transition(MissionPhase.COMPLETE, time_s)
        return False
