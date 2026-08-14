from a3docklab.config import GuidanceConfig
from a3docklab.simulation.phases import MissionPhase, PhaseMachine


def test_hold_point_requires_dwell_and_low_speed() -> None:
    machine = PhaseMachine(GuidanceConfig(hold_duration_s=10.0))
    assert machine.advance(1000.0, 0.0, 0.15)
    assert machine.advance(250.0, 1.0, 0.15)
    assert machine.phase == MissionPhase.HOLD_POINT_1
    assert not machine.advance(240.0, 20.0, 0.15, hold_speed_m_s=0.2)
    assert machine.advance(240.0, 20.0, 0.15, hold_speed_m_s=0.01)
    assert machine.phase == MissionPhase.PROXIMITY_OPERATIONS


def test_final_approach_requires_authorized_phase_sequence() -> None:
    machine = PhaseMachine(GuidanceConfig(hold_duration_s=0.0))
    machine.advance(1000.0, 0.0, 0.15)
    machine.advance(20.0, 1.0, 0.15)
    assert machine.phase == MissionPhase.HOLD_POINT_1
    assert machine.phase != MissionPhase.FINAL_APPROACH
