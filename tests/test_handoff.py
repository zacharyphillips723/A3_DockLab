import numpy as np

from a3docklab.config import HandoffConfig
from a3docklab.control.handoff import (
    AuthorityHandoff,
    AuthorityOwner,
    HandoffExchangePacket,
    HandoffInputs,
    HandoffState,
    authority_observation_valid,
    command_delta_norm,
    shadow_stack_wrench,
    validate_exchange_packet,
)


def test_nominal_handoff_changes_owner_only_after_acknowledgement() -> None:
    handoff = AuthorityHandoff(AuthorityOwner.TARGET, HandoffConfig(quiet_period_s=2.0))
    handoff.begin(10.0)
    handoff.advance(11.0, HandoffInputs())
    handoff.advance(12.0, HandoffInputs())
    assert handoff.state == HandoffState.QUIET_PERIOD
    handoff.advance(13.0, HandoffInputs())
    assert handoff.state == HandoffState.TRANSFER_PENDING
    handoff.advance(14.0, HandoffInputs())
    assert handoff.state == HandoffState.ACKNOWLEDGED
    assert handoff.owner == AuthorityOwner.ORION
    handoff.advance(15.0, HandoffInputs())
    assert handoff.state == HandoffState.ACTIVE
    assert handoff.owner == AuthorityOwner.TARGET


def test_lost_readiness_rolls_back_without_losing_original_owner() -> None:
    handoff = AuthorityHandoff(AuthorityOwner.TARGET, HandoffConfig())
    handoff.begin(0.0)
    update = handoff.advance(1.0, HandoffInputs(clock_synchronized=False))
    assert update.reason == "readiness_lost"
    assert handoff.state == HandoffState.ROLLBACK
    assert handoff.owner == AuthorityOwner.ORION


def test_shadow_command_discontinuity_rolls_back() -> None:
    config = HandoffConfig(quiet_period_s=0.0, maximum_command_discontinuity=1.0)
    handoff = AuthorityHandoff(AuthorityOwner.TARGET, config)
    handoff.begin(0.0)
    handoff.advance(1.0, HandoffInputs())
    handoff.advance(2.0, HandoffInputs())
    update = handoff.advance(3.0, HandoffInputs(shadow_command_delta=2.0))
    assert update.reason == "shadow_command_mismatch"
    assert handoff.owner == AuthorityOwner.ORION


def test_missing_acknowledgement_times_out_to_rollback() -> None:
    config = HandoffConfig(quiet_period_s=0.0, acknowledgement_timeout_s=1.0)
    handoff = AuthorityHandoff(AuthorityOwner.TARGET, config)
    handoff.begin(0.0)
    handoff.advance(1.0, HandoffInputs())
    handoff.advance(2.0, HandoffInputs())
    handoff.advance(3.0, HandoffInputs(acknowledgement_received=False))
    update = handoff.advance(4.1, HandoffInputs(acknowledgement_received=False))
    assert update.reason == "acknowledgement_timeout"
    assert handoff.state == HandoffState.ROLLBACK
    assert handoff.owner == AuthorityOwner.ORION


def test_exchange_validation_rejects_stale_and_wrong_frame_packets() -> None:
    packet = HandoffExchangePacket(
        schema_version="2.0",
        sequence_number=1,
        source_time_s=10.0,
        frame_id="wrong_frame",
        mission_phase="soft_capture",
        position_m=(0.0, 0.0, 0.0),
        velocity_m_s=(0.0, 0.0, 0.0),
        attitude_quaternion_wxyz=(1.0, 0.0, 0.0, 0.0),
        angular_rate_rad_s=(0.0, 0.0, 0.0),
        covariance=np.eye(12),
        actuator_health_fraction=1.0,
    )
    validation = validate_exchange_packet(
        packet,
        receive_time_s=12.0,
        expected_phase="soft_capture",
        config=HandoffConfig(maximum_data_age_s=0.5),
    )
    assert not validation.valid
    assert not validation.clock_synchronized
    assert not validation.frame_consistent


def test_shadow_commands_are_bounded_and_comparable() -> None:
    from a3docklab.config import load_config

    config = load_config("configs/scenarios/starship_nose.yaml")
    velocity = np.array([0.01, -0.02, 0.0])
    rate = np.array([0.0, 0.0, 1e-4])
    inertia = np.diag([1e6, 2e6, 3e6])
    orion = shadow_stack_wrench(velocity, rate, 100_000.0, inertia, config.chaser, config.handoff)
    target = shadow_stack_wrench(velocity, rate, 100_000.0, inertia, config.target, config.handoff)
    assert np.all(np.abs(orion.force_reference_n) <= config.chaser.max_translation_thrust_n)
    assert command_delta_norm(orion, target, config.chaser.diameter_m) >= 0.0


def test_authority_observation_requires_exactly_one_owner() -> None:
    assert authority_observation_valid(True, False)
    assert authority_observation_valid(False, True)
    assert not authority_observation_valid(True, True)
    assert not authority_observation_valid(False, False)
