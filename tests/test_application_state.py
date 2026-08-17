import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from a3docklab.application.state import (
    ApplicationStateStore,
    RunReview,
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
