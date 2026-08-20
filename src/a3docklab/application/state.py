"""Lakebase-compatible mutable state for mission-review workflows."""

from __future__ import annotations

import hashlib
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


class DurableSession(BaseModel):
    session_id: str
    scenario_id: str
    owner: str
    status: Literal["paused", "running", "complete", "terminated"]
    version: int = 0
    lease_holder: str | None = None
    lease_token_hash: str | None = None
    lease_expires_at_utc: datetime | None = None
    checkpoint: dict[str, Any] | None = None
    updated_at_utc: datetime


class AcceptedCommand(BaseModel):
    session_id: str
    command_id: str
    idempotency_key: str
    actor: str
    payload: dict[str, Any]
    accepted_at_utc: datetime


class PostgresConnectionFactory:
    """Create Lakebase connections from Databricks App resource variables."""

    def __call__(self) -> Any:
        try:
            import psycopg
            from databricks.sdk.core import Config
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

    def _execute_count(self, statement: str, parameters: Sequence[object] = ()) -> int:
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(statement, tuple(parameters))
                count = int(cursor.rowcount)
                connection.commit()
                return count
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
            """CREATE TABLE IF NOT EXISTS simulation_sessions (
                session_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL, owner TEXT NOT NULL,
                status TEXT NOT NULL, version BIGINT NOT NULL, lease_holder TEXT,
                lease_token_hash TEXT, lease_expires_at_utc TEXT, checkpoint_json TEXT,
                updated_at_utc TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS accepted_commands (
                session_id TEXT NOT NULL, command_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, actor TEXT NOT NULL, payload_json TEXT NOT NULL,
                accepted_at_utc TEXT NOT NULL, PRIMARY KEY (session_id, command_id),
                UNIQUE (session_id, idempotency_key))""",
        )
        for statement in statements:
            self._execute(statement)

    def healthcheck(self) -> bool:
        return self._execute("SELECT 1", fetch=True) == [(1,)]

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

    def create_durable_session(self, session: DurableSession) -> None:
        marks = ", ".join([self.placeholder] * 10)
        self._execute(
            f"INSERT INTO simulation_sessions VALUES ({marks})",
            (
                session.session_id,
                session.scenario_id,
                session.owner,
                session.status,
                session.version,
                session.lease_holder,
                session.lease_token_hash,
                session.lease_expires_at_utc.isoformat() if session.lease_expires_at_utc else None,
                json.dumps(session.checkpoint, sort_keys=True) if session.checkpoint else None,
                session.updated_at_utc.isoformat(),
            ),
        )

    def get_durable_session(self, session_id: str) -> DurableSession | None:
        rows = self._execute(
            "SELECT session_id, scenario_id, owner, status, version, lease_holder, "
            "lease_token_hash, lease_expires_at_utc, checkpoint_json, updated_at_utc "
            f"FROM simulation_sessions WHERE session_id = {self.placeholder}",
            (session_id,),
            fetch=True,
        )
        if not rows:
            return None
        row = rows[0]
        return DurableSession(
            session_id=row[0],
            scenario_id=row[1],
            owner=row[2],
            status=row[3],
            version=row[4],
            lease_holder=row[5],
            lease_token_hash=row[6],
            lease_expires_at_utc=row[7],
            checkpoint=json.loads(row[8]) if row[8] else None,
            updated_at_utc=row[9],
        )

    def acquire_session_lease(
        self,
        session_id: str,
        holder: str,
        token_hash: str,
        expires_at_utc: datetime,
        expected_version: int,
        now_utc: datetime | None = None,
    ) -> DurableSession:
        now = now_utc or self._now()
        count = self._execute_count(
            f"UPDATE simulation_sessions SET lease_holder = {self.placeholder}, lease_token_hash = {self.placeholder}, "
            f"lease_expires_at_utc = {self.placeholder}, version = version + 1, updated_at_utc = {self.placeholder} "
            f"WHERE session_id = {self.placeholder} AND version = {self.placeholder} AND "
            f"(lease_holder IS NULL OR lease_expires_at_utc < {self.placeholder} OR lease_holder = {self.placeholder})",
            (
                holder,
                token_hash,
                expires_at_utc.isoformat(),
                now.isoformat(),
                session_id,
                expected_version,
                now.isoformat(),
                holder,
            ),
        )
        if count != 1:
            raise RuntimeError("session lease conflict")
        result = self.get_durable_session(session_id)
        assert result is not None
        return result

    def update_session_checkpoint(
        self,
        session_id: str,
        status: Literal["paused", "running", "complete", "terminated"],
        checkpoint: dict[str, Any],
        expected_version: int,
    ) -> DurableSession:
        count = self._execute_count(
            f"UPDATE simulation_sessions SET status = {self.placeholder}, checkpoint_json = {self.placeholder}, "
            f"version = version + 1, updated_at_utc = {self.placeholder} "
            f"WHERE session_id = {self.placeholder} AND version = {self.placeholder}",
            (
                status,
                json.dumps(checkpoint, sort_keys=True),
                self._now().isoformat(),
                session_id,
                expected_version,
            ),
        )
        if count != 1:
            raise RuntimeError("session version conflict")
        result = self.get_durable_session(session_id)
        assert result is not None
        return result

    def record_accepted_command(self, command: AcceptedCommand) -> AcceptedCommand:
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            try:
                marks = ", ".join([self.placeholder] * 6)
                cursor.execute(
                    f"INSERT INTO accepted_commands VALUES ({marks}) ON CONFLICT DO NOTHING",
                    (
                        command.session_id,
                        command.command_id,
                        command.idempotency_key,
                        command.actor,
                        json.dumps(command.payload, sort_keys=True),
                        command.accepted_at_utc.isoformat(),
                    ),
                )
                cursor.execute(
                    "SELECT session_id, command_id, idempotency_key, actor, payload_json, "
                    f"accepted_at_utc FROM accepted_commands WHERE session_id = {self.placeholder} "
                    f"AND idempotency_key = {self.placeholder}",
                    (command.session_id, command.idempotency_key),
                )
                row = cursor.fetchone()
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()
        if row is None:
            raise RuntimeError("command identifier conflict")
        saved = AcceptedCommand(
            session_id=row[0],
            command_id=row[1],
            idempotency_key=row[2],
            actor=row[3],
            payload=json.loads(row[4]),
            accepted_at_utc=row[5],
        )
        if saved.payload != command.payload:
            raise RuntimeError("idempotency key reused with different payload")
        return saved


def hash_lease_token(token: str) -> str:
    """Return the non-reversible representation persisted for a session lease."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
