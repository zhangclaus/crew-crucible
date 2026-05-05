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


SQLITE_EXPECTED_SCHEMA_VERSION = 2
SQLITE_SCHEMA_MIGRATIONS = [
    {"version": 1, "checksum": "sqlite_events_v1"},
    {"version": 2, "checksum": "sqlite_events_round_contract_v2"},
]
SQLITE_REQUIRED_COLUMNS = [
    "event_id",
    "stream_id",
    "sequence",
    "type",
    "crew_id",
    "worker_id",
    "turn_id",
    "round_id",
    "contract_id",
    "idempotency_key",
    "payload_json",
    "artifact_refs_json",
    "created_at",
]


class SQLiteEventStore:
    def __init__(
        self,
        path: Path,
        *,
        initialize: bool = True,
        readonly: bool = False,
    ) -> None:
        self.path = path
        self._readonly = readonly
        if initialize:
            if readonly:
                raise ValueError("readonly event stores cannot initialize schema")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    @classmethod
    def open_existing(cls, path: Path) -> "SQLiteEventStore":
        if not path.exists():
            raise FileNotFoundError(path)
        return cls(path, initialize=False, readonly=True)

    def append(
        self,
        *,
        stream_id: str,
        type: str,
        crew_id: str = "",
        worker_id: str = "",
        turn_id: str = "",
        round_id: str = "",
        contract_id: str = "",
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
                round_id=round_id,
                contract_id=contract_id,
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
        round_id: str = "",
        contract_id: str = "",
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
                round_id=round_id,
                contract_id=contract_id,
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
            rows = self._execute_read(
                conn,
                """
                SELECT * FROM events
                WHERE stream_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (stream_id, after_sequence),
            )
        return [self._row_to_event(row) for row in rows]

    def list_by_turn(self, turn_id: str) -> list[AgentEvent]:
        with self._connection() as conn:
            rows = self._execute_read(
                conn,
                """
                SELECT * FROM events
                WHERE turn_id = ?
                ORDER BY rowid ASC
                """,
                (turn_id,),
            )
        return [self._row_to_event(row) for row in rows]

    def list_all(self) -> list[AgentEvent]:
        with self._connection() as conn:
            rows = self._execute_read(
                conn,
                """
                SELECT * FROM events
                ORDER BY rowid ASC
                """
            )
        return [self._row_to_event(row) for row in rows]

    def get_by_idempotency_key(self, idempotency_key: str) -> AgentEvent | None:
        if not idempotency_key:
            return None

        with self._connection() as conn:
            try:
                return self._get_by_idempotency_key(conn, idempotency_key)
            except sqlite3.OperationalError as exc:
                if self._is_missing_events_table(exc):
                    return None
                raise

    def health(self) -> dict[str, Any]:
        try:
            with self._connection() as conn:
                table_names = {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                initialized = "events" in table_names
                columns = _sqlite_columns(conn, "events") if initialized else []
                missing_columns = [
                    column for column in SQLITE_REQUIRED_COLUMNS if column not in columns
                ]
                migrations = _sqlite_migrations(conn) if "event_store_schema_migrations" in table_names else []
                latest_schema_version = max(
                    (migration["version"] for migration in migrations),
                    default=0,
                )
        except Exception as exc:
            return {
                "backend": "sqlite",
                "ok": False,
                "readonly": self._readonly,
                "initialized": False,
                "expected_schema_version": SQLITE_EXPECTED_SCHEMA_VERSION,
                "latest_schema_version": 0,
                "applied_migrations": [],
                "missing_columns": SQLITE_REQUIRED_COLUMNS,
                "error": str(exc),
            }
        return {
            "backend": "sqlite",
            "ok": initialized
            and latest_schema_version >= SQLITE_EXPECTED_SCHEMA_VERSION
            and not missing_columns,
            "readonly": self._readonly,
            "initialized": initialized,
            "expected_schema_version": SQLITE_EXPECTED_SCHEMA_VERSION,
            "latest_schema_version": latest_schema_version,
            "applied_migrations": migrations,
            "missing_columns": missing_columns,
            "path": str(self.path),
        }

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS event_store_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
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
                    round_id TEXT NOT NULL DEFAULT '',
                    contract_id TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (stream_id, sequence)
                )
                """
            )
            existing_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(events)").fetchall()
            }
            if "round_id" not in existing_columns:
                conn.execute("ALTER TABLE events ADD COLUMN round_id TEXT NOT NULL DEFAULT ''")
            if "contract_id" not in existing_columns:
                conn.execute("ALTER TABLE events ADD COLUMN contract_id TEXT NOT NULL DEFAULT ''")
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
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_round_id
                ON events (round_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_contract_id
                ON events (contract_id)
                """
            )
            for migration in SQLITE_SCHEMA_MIGRATIONS:
                conn.execute(
                    """
                    INSERT INTO event_store_schema_migrations (version, checksum, applied_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    (
                        migration["version"],
                        migration["checksum"],
                        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    ),
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
        if self._readonly:
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
        else:
            conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _execute_read(
        self,
        conn: sqlite3.Connection,
        statement: str,
        parameters: tuple[Any, ...] = (),
    ) -> list[sqlite3.Row]:
        try:
            return conn.execute(statement, parameters).fetchall()
        except sqlite3.OperationalError as exc:
            if self._is_missing_events_table(exc):
                return []
            raise

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
                round_id,
                contract_id,
                idempotency_key,
                payload_json,
                artifact_refs_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.stream_id,
                event.sequence,
                event.type,
                event.crew_id,
                event.worker_id,
                event.turn_id,
                event.round_id,
                event.contract_id,
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

    @staticmethod
    def _is_missing_events_table(error: sqlite3.OperationalError) -> bool:
        return "no such table: events" in str(error).lower()

    def _row_to_event(self, row: sqlite3.Row) -> AgentEvent:
        keys = set(row.keys())
        return AgentEvent(
            event_id=row["event_id"],
            stream_id=row["stream_id"],
            sequence=row["sequence"],
            type=row["type"],
            crew_id=row["crew_id"],
            worker_id=row["worker_id"],
            turn_id=row["turn_id"],
            round_id=row["round_id"] if "round_id" in keys else "",
            contract_id=row["contract_id"] if "contract_id" in keys else "",
            idempotency_key=row["idempotency_key"],
            payload=json.loads(row["payload_json"]),
            artifact_refs=json.loads(row["artifact_refs_json"]),
            created_at=row["created_at"],
        )


def _sqlite_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]


def _sqlite_migrations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT version, checksum
        FROM event_store_schema_migrations
        ORDER BY version ASC
        """
    ).fetchall()
    return [{"version": int(row["version"]), "checksum": str(row["checksum"])} for row in rows]
