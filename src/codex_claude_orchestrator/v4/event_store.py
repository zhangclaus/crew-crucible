"""SQLite-backed event storage for the durable V4 runtime."""

from __future__ import annotations

from contextlib import closing, contextmanager
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from codex_claude_orchestrator.v4.events import AgentEvent, normalize


class SQLiteEventStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def append(
        self,
        *,
        stream_id: str,
        type: str,
        crew_id: str = "",
        worker_id: str = "",
        turn_id: str = "",
        idempotency_key: str = "",
        payload: dict[str, Any] | None = None,
        artifact_refs: list[str] | None = None,
        created_at: str = "",
    ) -> AgentEvent:
        with self._write_transaction() as conn:
            if idempotency_key:
                existing = self._get_by_idempotency_key(conn, idempotency_key)
                if existing is not None:
                    return existing

            sequence = self._next_sequence(conn, stream_id)
            event = AgentEvent(
                event_id=f"evt-{uuid4().hex}",
                stream_id=stream_id,
                sequence=sequence,
                type=type,
                crew_id=crew_id,
                worker_id=worker_id,
                turn_id=turn_id,
                idempotency_key=idempotency_key,
                payload=payload or {},
                artifact_refs=artifact_refs or [],
                created_at=created_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
            try:
                self._insert_event(conn, event)
            except sqlite3.IntegrityError:
                if idempotency_key:
                    existing = self._get_by_idempotency_key(conn, idempotency_key)
                    if existing is not None:
                        return existing
                raise
            return event

    def append_claim(
        self,
        *,
        stream_id: str,
        type: str,
        crew_id: str = "",
        worker_id: str = "",
        turn_id: str = "",
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
        artifact_refs: list[str] | None = None,
        created_at: str = "",
    ) -> tuple[AgentEvent, bool]:
        with self._write_transaction() as conn:
            existing = self._get_by_idempotency_key(conn, idempotency_key)
            if existing is not None:
                return existing, False

            sequence = self._next_sequence(conn, stream_id)
            event = AgentEvent(
                event_id=f"evt-{uuid4().hex}",
                stream_id=stream_id,
                sequence=sequence,
                type=type,
                crew_id=crew_id,
                worker_id=worker_id,
                turn_id=turn_id,
                idempotency_key=idempotency_key,
                payload=payload or {},
                artifact_refs=artifact_refs or [],
                created_at=created_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
            try:
                self._insert_event(conn, event)
            except sqlite3.IntegrityError:
                existing = self._get_by_idempotency_key(conn, idempotency_key)
                if existing is not None:
                    return existing, False
                raise
            return event, True

    def list_stream(self, stream_id: str, after_sequence: int = 0) -> list[AgentEvent]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE stream_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (stream_id, after_sequence),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_by_turn(self, turn_id: str) -> list[AgentEvent]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE turn_id = ?
                ORDER BY rowid ASC
                """,
                (turn_id,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_all(self) -> list[AgentEvent]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                ORDER BY rowid ASC
                """
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def get_by_idempotency_key(self, idempotency_key: str) -> AgentEvent | None:
        if not idempotency_key:
            return None

        with self._connection() as conn:
            return self._get_by_idempotency_key(conn, idempotency_key)

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    stream_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    crew_id TEXT NOT NULL DEFAULT '',
                    worker_id TEXT NOT NULL DEFAULT '',
                    turn_id TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (stream_id, sequence)
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idempotency_key_non_empty
                ON events (idempotency_key)
                WHERE idempotency_key != ''
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_turn_id
                ON events (turn_id)
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as conn:
            with conn:
                yield conn

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _insert_event(self, conn: sqlite3.Connection, event: AgentEvent) -> None:
        conn.execute(
            """
            INSERT INTO events (
                event_id,
                stream_id,
                sequence,
                type,
                crew_id,
                worker_id,
                turn_id,
                idempotency_key,
                payload_json,
                artifact_refs_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.stream_id,
                event.sequence,
                event.type,
                event.crew_id,
                event.worker_id,
                event.turn_id,
                event.idempotency_key,
                json.dumps(normalize(event.payload), sort_keys=True),
                json.dumps(normalize(event.artifact_refs), sort_keys=True),
                event.created_at,
            ),
        )

    def _get_by_idempotency_key(
        self,
        conn: sqlite3.Connection,
        idempotency_key: str,
    ) -> AgentEvent | None:
        row = conn.execute(
            "SELECT * FROM events WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return self._row_to_event(row) if row is not None else None

    def _next_sequence(self, conn: sqlite3.Connection, stream_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM events WHERE stream_id = ?",
            (stream_id,),
        ).fetchone()
        return int(row["next_sequence"])

    def _row_to_event(self, row: sqlite3.Row) -> AgentEvent:
        return AgentEvent(
            event_id=row["event_id"],
            stream_id=row["stream_id"],
            sequence=row["sequence"],
            type=row["type"],
            crew_id=row["crew_id"],
            worker_id=row["worker_id"],
            turn_id=row["turn_id"],
            idempotency_key=row["idempotency_key"],
            payload=json.loads(row["payload_json"]),
            artifact_refs=json.loads(row["artifact_refs_json"]),
            created_at=row["created_at"],
        )
