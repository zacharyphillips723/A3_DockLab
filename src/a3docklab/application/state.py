"""Lakebase-compatible mutable state for mission-review workflows."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel


class Annotation(BaseModel):
    annotation_id: str
    run_id: str
    author: str
    text: str
    event_time_ns: int | None = None
    created_at_utc: datetime


class SavedView(BaseModel):
    view_id: str
    owner: str
    name: str
    run_id: str
    start_time_ns: int | None = None
    end_time_ns: int | None = None
    channels: tuple[str, ...] = ()
    updated_at_utc: datetime


class RunReview(BaseModel):
    run_id: str
    reviewer: str
    status: Literal["pending", "in_review", "approved", "rejected"] = "pending"
    notes: str = ""
    updated_at_utc: datetime


class SavedComparison(BaseModel):
    comparison_id: str
    owner: str
    name: str
    baseline_run_id: str
    candidate_run_id: str
    alignment: Literal["event_time", "mission_phase"] = "event_time"
    updated_at_utc: datetime


class PostgresConnectionFactory:
    """Create Lakebase connections from Databricks App resource variables."""

    def __call__(self) -> Any:
        try:
            import psycopg  # type: ignore[import-not-found]
            from databricks.sdk.core import Config  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "Lakebase state requires the 'databricks' optional dependency"
            ) from exc
        authorization = Config().authenticate()["Authorization"]
        password = authorization.removeprefix("Bearer ")
        return psycopg.connect(
            host=os.environ["PGHOST"],
            port=int(os.environ.get("PGPORT", "5432")),
            dbname=os.environ["PGDATABASE"],
            user=os.environ["PGUSER"],
            password=password,
            sslmode=os.environ.get("PGSSLMODE", "require"),
            application_name=os.environ.get("PGAPPNAME", "a3-docklab"),
        )


class ApplicationStateStore:
    """Transactional repository for operator-authored state."""

    def __init__(self, connection_factory: Callable[[], Any], placeholder: str = "%s") -> None:
        self.connection_factory = connection_factory
        self.placeholder = placeholder

    def _execute(
        self, statement: str, parameters: Sequence[object] = (), *, fetch: bool = False
    ) -> list[tuple[Any, ...]]:
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, tuple(parameters))
                rows = list(cursor.fetchall()) if fetch else []
                connection.commit()
                return rows
            finally:
                cursor.close()
        finally:
            connection.close()

    def initialize(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS annotations (
                annotation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, author TEXT NOT NULL,
                text TEXT NOT NULL, event_time_ns BIGINT, created_at_utc TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS saved_views (
                view_id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                run_id TEXT NOT NULL, start_time_ns BIGINT, end_time_ns BIGINT,
                channels_json TEXT NOT NULL, updated_at_utc TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS run_reviews (
                run_id TEXT NOT NULL, reviewer TEXT NOT NULL, status TEXT NOT NULL,
                notes TEXT NOT NULL, updated_at_utc TEXT NOT NULL,
                PRIMARY KEY (run_id, reviewer))""",
            """CREATE TABLE IF NOT EXISTS saved_comparisons (
                comparison_id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                baseline_run_id TEXT NOT NULL, candidate_run_id TEXT NOT NULL,
                alignment TEXT NOT NULL, updated_at_utc TEXT NOT NULL)""",
        )
        for statement in statements:
            self._execute(statement)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def add_annotation(
        self,
        run_id: str,
        author: str,
        text: str,
        event_time_ns: int | None = None,
    ) -> Annotation:
        annotation = Annotation(
            annotation_id=str(uuid.uuid4()),
            run_id=run_id,
            author=author,
            text=text,
            event_time_ns=event_time_ns,
            created_at_utc=self._now(),
        )
        values = annotation.model_dump(mode="json")
        marks = ", ".join([self.placeholder] * 6)
        self._execute(
            f"INSERT INTO annotations VALUES ({marks})",
            tuple(values.values()),
        )
        return annotation

    def list_annotations(self, run_id: str) -> list[Annotation]:
        rows = self._execute(
            f"SELECT annotation_id, run_id, author, text, event_time_ns, created_at_utc "
            f"FROM annotations WHERE run_id = {self.placeholder} ORDER BY created_at_utc",
            (run_id,),
            fetch=True,
        )
        return [
            Annotation.model_validate(dict(zip(Annotation.model_fields, row, strict=True)))
            for row in rows
        ]

    def save_view(self, view: SavedView) -> None:
        marks = ", ".join([self.placeholder] * 8)
        update = ", ".join(
            f"{field} = excluded.{field}"
            for field in (
                "owner",
                "name",
                "run_id",
                "start_time_ns",
                "end_time_ns",
                "channels_json",
                "updated_at_utc",
            )
        )
        self._execute(
            f"INSERT INTO saved_views VALUES ({marks}) ON CONFLICT (view_id) DO UPDATE SET {update}",
            (
                view.view_id,
                view.owner,
                view.name,
                view.run_id,
                view.start_time_ns,
                view.end_time_ns,
                json.dumps(view.channels),
                view.updated_at_utc.isoformat(),
            ),
        )

    def list_views(self, owner: str) -> list[SavedView]:
        rows = self._execute(
            f"SELECT view_id, owner, name, run_id, start_time_ns, end_time_ns, "
            f"channels_json, updated_at_utc FROM saved_views WHERE owner = {self.placeholder} "
            "ORDER BY name",
            (owner,),
            fetch=True,
        )
        return [
            SavedView(
                view_id=row[0],
                owner=row[1],
                name=row[2],
                run_id=row[3],
                start_time_ns=row[4],
                end_time_ns=row[5],
                channels=tuple(json.loads(row[6])),
                updated_at_utc=row[7],
            )
            for row in rows
        ]

    def upsert_review(self, review: RunReview) -> None:
        marks = ", ".join([self.placeholder] * 5)
        self._execute(
            f"INSERT INTO run_reviews VALUES ({marks}) ON CONFLICT (run_id, reviewer) "
            "DO UPDATE SET status = excluded.status, notes = excluded.notes, "
            "updated_at_utc = excluded.updated_at_utc",
            (
                review.run_id,
                review.reviewer,
                review.status,
                review.notes,
                review.updated_at_utc.isoformat(),
            ),
        )

    def get_reviews(self, run_id: str) -> list[RunReview]:
        rows = self._execute(
            f"SELECT run_id, reviewer, status, notes, updated_at_utc FROM run_reviews "
            f"WHERE run_id = {self.placeholder} ORDER BY reviewer",
            (run_id,),
            fetch=True,
        )
        return [
            RunReview.model_validate(dict(zip(RunReview.model_fields, row, strict=True)))
            for row in rows
        ]

    def save_comparison(self, comparison: SavedComparison) -> None:
        marks = ", ".join([self.placeholder] * 7)
        self._execute(
            f"INSERT INTO saved_comparisons VALUES ({marks}) ON CONFLICT (comparison_id) "
            "DO UPDATE SET owner = excluded.owner, name = excluded.name, "
            "baseline_run_id = excluded.baseline_run_id, candidate_run_id = excluded.candidate_run_id, "
            "alignment = excluded.alignment, updated_at_utc = excluded.updated_at_utc",
            tuple(comparison.model_dump(mode="json").values()),
        )

    def list_comparisons(self, owner: str) -> list[SavedComparison]:
        rows = self._execute(
            f"SELECT comparison_id, owner, name, baseline_run_id, candidate_run_id, alignment, "
            f"updated_at_utc FROM saved_comparisons WHERE owner = {self.placeholder} ORDER BY name",
            (owner,),
            fetch=True,
        )
        return [
            SavedComparison.model_validate(
                dict(zip(SavedComparison.model_fields, row, strict=True))
            )
            for row in rows
        ]


def new_saved_view(owner: str, name: str, run_id: str, **window: Any) -> SavedView:
    return SavedView(
        view_id=str(uuid.uuid4()),
        owner=owner,
        name=name,
        run_id=run_id,
        updated_at_utc=datetime.now(UTC),
        **window,
    )


def new_comparison(
    owner: str,
    name: str,
    baseline_run_id: str,
    candidate_run_id: str,
    alignment: Literal["event_time", "mission_phase"] = "event_time",
) -> SavedComparison:
    return SavedComparison(
        comparison_id=str(uuid.uuid4()),
        owner=owner,
        name=name,
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        alignment=alignment,
        updated_at_utc=datetime.now(UTC),
    )
