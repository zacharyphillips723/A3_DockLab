import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from a3docklab.application.state import (
    AcceptedCommand,
    ApplicationStateStore,
    DurableSession,
    RunReview,
    hash_lease_token,
    new_comparison,
    new_saved_view,
)


def _store(path: Path) -> ApplicationStateStore:
    store = ApplicationStateStore(lambda: sqlite3.connect(path), placeholder="?")
    store.initialize()
    return store


def test_application_state_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.db")
    annotation = store.add_annotation("run-1", "operator@example.com", "Inspect drift", 42)
    assert store.list_annotations("run-1") == [annotation]

    view = new_saved_view(
        "operator@example.com",
        "Terminal approach",
        "run-1",
        start_time_ns=10,
        end_time_ns=100,
        channels=("range_m", "closing_rate_m_s"),
    )
    store.save_view(view)
    assert store.list_views("operator@example.com") == [view]

    review = RunReview(
        run_id="run-1",
        reviewer="operator@example.com",
        status="approved",
        notes="Nominal capture",
        updated_at_utc=datetime.now(UTC),
    )
    store.upsert_review(review)
    assert store.get_reviews("run-1") == [review]
    history = store.get_review_history("run-1")
    assert len(history) == 1
    assert history[0].previous_status is None
    assert history[0].status == "approved"

    comparison = new_comparison(
        "operator@example.com", "Nominal vs fault", "run-1", "run-2", "mission_phase"
    )
    store.save_comparison(comparison)
    assert store.list_comparisons("operator@example.com") == [comparison]


def test_application_state_is_owner_and_run_scoped(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.db")
    store.add_annotation("run-a", "a@example.com", "A")
    store.add_annotation("run-b", "b@example.com", "B")
    store.save_view(new_saved_view("a@example.com", "A view", "run-a"))
    store.save_view(new_saved_view("b@example.com", "B view", "run-b"))

    assert [item.text for item in store.list_annotations("run-a")] == ["A"]
    assert [item.name for item in store.list_views("a@example.com")] == ["A view"]


def test_review_updates_preserve_immutable_attributed_history(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.db")
    first = RunReview(
        run_id="run-1",
        reviewer="reviewer@example.com",
        status="in_review",
        notes="Checking capture",
        updated_at_utc=datetime(2026, 8, 21, 12, tzinfo=UTC),
    )
    approved = first.model_copy(
        update={
            "status": "approved",
            "notes": "Capture verified",
            "updated_at_utc": datetime(2026, 8, 21, 13, tzinfo=UTC),
        }
    )
    store.upsert_review(first)
    store.upsert_review(approved)

    assert store.get_reviews("run-1") == [approved]
    history = store.get_review_history("run-1")
    assert [event.status for event in history] == ["in_review", "approved"]
    assert [event.previous_status for event in history] == [None, "in_review"]
    assert all(event.reviewer == "reviewer@example.com" for event in history)


def test_durable_session_checkpoint_survives_store_restart(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    store = _store(path)
    created_at = datetime.now(UTC)
    store.create_durable_session(
        DurableSession(
            session_id="session-1",
            scenario_id="nominal",
            owner="operator@example.com",
            status="paused",
            updated_at_utc=created_at,
        )
    )

    leased = store.acquire_session_lease(
        "session-1",
        "browser-1",
        hash_lease_token("secret-token"),
        created_at + timedelta(minutes=1),
        expected_version=0,
        now_utc=created_at,
    )
    checkpointed = store.update_session_checkpoint(
        "session-1",
        "running",
        {"simulation_time_s": 12.5, "state": {"range_m": 42.0}},
        expected_version=leased.version,
    )

    restored = _store(path).get_durable_session("session-1")
    assert restored == checkpointed
    assert restored is not None
    assert restored.checkpoint == {"simulation_time_s": 12.5, "state": {"range_m": 42.0}}
    assert restored.lease_token_hash == hash_lease_token("secret-token")
    assert restored.lease_token_hash != "secret-token"


def test_session_lease_is_exclusive_and_uses_optimistic_version(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.db")
    now = datetime.now(UTC)
    store.create_durable_session(
        DurableSession(
            session_id="session-1",
            scenario_id="nominal",
            owner="operator@example.com",
            status="paused",
            updated_at_utc=now,
        )
    )
    first = store.acquire_session_lease(
        "session-1", "browser-1", hash_lease_token("one"), now + timedelta(seconds=30), 0, now
    )

    with pytest.raises(RuntimeError, match="lease conflict"):
        store.acquire_session_lease(
            "session-1", "browser-2", hash_lease_token("two"), now + timedelta(minutes=1), 1, now
        )
    with pytest.raises(RuntimeError, match="lease conflict"):
        store.acquire_session_lease(
            "session-1",
            "browser-1",
            hash_lease_token("one"),
            now + timedelta(minutes=1),
            0,
            now,
        )

    takeover = store.acquire_session_lease(
        "session-1",
        "browser-2",
        hash_lease_token("two"),
        now + timedelta(minutes=2),
        first.version,
        now + timedelta(seconds=31),
    )
    assert takeover.lease_holder == "browser-2"
    assert takeover.version == 2


def test_session_lease_renewal_requires_current_token_and_unexpired_lease(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "state.db")
    now = datetime.now(UTC)
    store.create_durable_session(
        DurableSession(
            session_id="session-1",
            scenario_id="nominal",
            owner="operator@example.com",
            status="paused",
            updated_at_utc=now,
        )
    )
    leased = store.acquire_session_lease(
        "session-1", "app-1", hash_lease_token("one"), now + timedelta(seconds=30), 0, now
    )
    renewed = store.renew_session_lease(
        "session-1",
        "app-1",
        hash_lease_token("one"),
        now + timedelta(minutes=1),
        leased.version,
        now + timedelta(seconds=10),
    )
    assert renewed.version == 2
    assert renewed.lease_expires_at_utc == now + timedelta(minutes=1)

    with pytest.raises(RuntimeError, match="invalid, expired, or stale"):
        store.renew_session_lease(
            "session-1",
            "app-1",
            hash_lease_token("wrong"),
            now + timedelta(minutes=2),
            renewed.version,
            now + timedelta(seconds=20),
        )
    with pytest.raises(RuntimeError, match="invalid, expired, or stale"):
        store.renew_session_lease(
            "session-1",
            "app-1",
            hash_lease_token("one"),
            now + timedelta(minutes=3),
            renewed.version,
            now + timedelta(minutes=2),
        )


def test_accepted_commands_are_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.db")
    command = AcceptedCommand(
        session_id="session-1",
        command_id="command-1",
        idempotency_key="request-1",
        actor="operator@example.com",
        payload={"kind": "hold"},
        accepted_at_utc=datetime.now(UTC),
    )

    assert store.record_accepted_command(command) == command
    assert (
        store.record_accepted_command(command.model_copy(update={"command_id": "retry"})) == command
    )
    with pytest.raises(RuntimeError, match="different payload"):
        store.record_accepted_command(
            command.model_copy(update={"command_id": "command-2", "payload": {"kind": "abort"}})
        )
