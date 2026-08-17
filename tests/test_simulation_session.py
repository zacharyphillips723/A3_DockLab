from pathlib import Path

import pandas as pd
import pytest

from a3docklab.config import load_config
from a3docklab.simulation.engine import SimulationCheckpoint, SimulationSession, run_controlled


@pytest.fixture(scope="module")
def scenario_path() -> Path:
    return Path(__file__).resolve().parents[1] / "configs/scenarios/blue_moon_side.yaml"


def test_session_starts_paused_and_single_step_is_exact(scenario_path: Path) -> None:
    config = load_config(scenario_path)
    session = SimulationSession(config)

    assert session.paused
    assert session.current is None
    assert session.advance(10) == []

    first = session.step()
    second = session.step()

    assert first.state["time_s"] == 0.0
    assert second.state["time_s"] == pytest.approx(config.step_s)
    assert session.step_index == 1


def test_reset_reproduces_initial_frame(scenario_path: Path) -> None:
    session = SimulationSession(load_config(scenario_path))
    first = session.step()
    session.step()
    session.reset()

    assert session.paused
    assert session.step_index == -1
    reset_first = session.step()
    pd.testing.assert_series_equal(
        pd.Series(reset_first.state), pd.Series(first.state), check_names=False
    )


def test_checkpoint_restore_reproduces_subsequent_frames(scenario_path: Path) -> None:
    session = SimulationSession(load_config(scenario_path))
    for _ in range(25):
        session.step()
    checkpoint = session.checkpoint()
    expected = [session.step() for _ in range(10)]

    restored = session.restore(checkpoint)
    actual = [session.step() for _ in range(10)]

    assert restored.state["time_s"] == checkpoint.time_s
    for expected_frame, actual_frame in zip(expected, actual, strict=True):
        pd.testing.assert_series_equal(
            pd.Series(actual_frame.state), pd.Series(expected_frame.state), check_names=False
        )
        assert actual_frame.events == expected_frame.events


def test_checkpoint_rejects_another_configuration(scenario_path: Path) -> None:
    session = SimulationSession(load_config(scenario_path))
    state = session.step().state
    checkpoint = SimulationCheckpoint("different-run", 0, 0.0, state)

    with pytest.raises(ValueError, match="different simulation configuration"):
        session.restore(checkpoint)


def test_batch_wrapper_matches_explicit_session(scenario_path: Path) -> None:
    config = load_config(scenario_path)
    batch = run_controlled(config)
    stepped = SimulationSession(config).run_to_completion()

    pd.testing.assert_frame_equal(batch.telemetry, stepped.telemetry)
    pd.testing.assert_frame_equal(batch.events, stepped.events)
