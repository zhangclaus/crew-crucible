"""PostgreSQL-backed event storage for the durable V4 runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from codex_claude_orchestrator.v4.events import AgentEvent, normalize


DEFAULT_PG_HOST = "124.222.58.173"
DEFAULT_PG_DB = "ragbase"
DEFAULT_PG_USER = "ragbase"
DEFAULT_PG_PORT = 5432
POSTGRES_EXPECTED_SCHEMA_VERSION = 2
POSTGRES_REQUIRED_COLUMNS = [
    "position",
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
    "payload_jsonb",
    "artifact_refs_jsonb",
    "created_at",
]


class PostgresConfigurationError(RuntimeError):
    """Raised when PostgreSQL event-store configuration is incomplete."""


class PostgresDriverError(RuntimeError):
    """Raised when the optional PostgreSQL driver is unavailable."""


@dataclass(frozen=True, slots=True)
class PostgresEventStoreConfig:
    host: str = DEFAULT_PG_HOST
    database: str = DEFAULT_PG_DB
    user: str = DEFAULT_PG_USER
    port: int = DEFAULT_PG_PORT
    password: str | None = None
    allow_default_endpoint: bool = False
    using_default_endpoint: bool = True

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "PostgresEventStoreConfig":
        env = environ or os.environ
        try:
            port = int(env.get("PG_PORT", str(DEFAULT_PG_PORT)))
        except ValueError as exc:
            raise PostgresConfigurationError("PG_PORT must be an integer") from exc
        explicit_endpoint = all(env.get(name) for name in ("PG_HOST", "PG_DB", "PG_USER"))
        return cls(
            host=env.get("PG_HOST", DEFAULT_PG_HOST),
            database=env.get("PG_DB", DEFAULT_PG_DB),
            user=env.get("PG_USER", DEFAULT_PG_USER),
            port=port,
            password=env.get("PG_PASSWORD") or None,
            allow_default_endpoint=env.get("PG_ALLOW_DEFAULT_ENDPOINT", "").lower() in {"1", "true", "yes", "y"},
            using_default_endpoint=not explicit_endpoint,
        )

    def require_password(self) -> str:
        if not self.password:
            raise PostgresConfigurationError("PG_PASSWORD is required for the PostgreSQL event store")
        return self.password

    def connect_kwargs(self) -> dict[str, Any]:
        if self.using_default_endpoint and not self.allow_default_endpoint:
            raise PostgresConfigurationError(
                "PG_ALLOW_DEFAULT_ENDPOINT=1 is required when PG_HOST, PG_DB, or PG_USER use deployment defaults"
            )
        return {
            "host": self.host,
            "dbname": self.database,
            "user": self.user,
            "password": self.require_password(),
            "port": self.port,
        }


class PostgresEventStore:
    """Production V4 event store using remote PostgreSQL.

    The psycopg dependency is imported lazily so local test runs can exercise
    configuration and protocol behavior without requiring a live database.
    """

    def __init__(self, config: PostgresEventStoreConfig | None = None):
        self.config = config or PostgresEventStoreConfig.from_env()

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                for statement in SCHEMA_STATEMENTS:
                    cursor.execute(statement)
            conn.commit()

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
        event, _inserted = self._append_event(
            stream_id=stream_id,
            type=type,
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=turn_id,
            round_id=round_id,
            contract_id=contract_id,
            idempotency_key=idempotency_key,
            payload=payload,
            artifact_refs=artifact_refs,
            created_at=created_at,
        )
        return event

    def _append_event(
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
    ) -> tuple[AgentEvent, bool]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                if idempotency_key:
                    existing = self._get_by_idempotency_key(cursor, idempotency_key)
                    if existing is not None:
                        conn.commit()
                        return existing, False

                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (stream_id,))
                sequence = self._next_sequence(cursor, stream_id)
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
                    created_at=created_at or _utc_now(),
                )
                inserted = self._insert_event(cursor, event)
                if not inserted and idempotency_key:
                    existing = self._get_by_idempotency_key(cursor, idempotency_key)
                    if existing is not None:
                        conn.commit()
                        return existing, False
            conn.commit()
            return event, True

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
        return self._append_event(
            stream_id=stream_id,
            type=type,
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=turn_id,
            round_id=round_id,
            contract_id=contract_id,
            idempotency_key=idempotency_key,
            payload=payload,
            artifact_refs=artifact_refs,
            created_at=created_at,
        )

    def list_stream(self, stream_id: str, after_sequence: int = 0) -> list[AgentEvent]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM agent_events
                    WHERE stream_id = %s AND sequence > %s
                    ORDER BY sequence ASC
                    """,
                    (stream_id, after_sequence),
                )
                rows = cursor.fetchall()
            conn.commit()
        return [self._row_to_event(row) for row in rows]

    def list_by_turn(self, turn_id: str) -> list[AgentEvent]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM agent_events
                    WHERE turn_id = %s
                    ORDER BY position ASC
                    """,
                    (turn_id,),
                )
                rows = cursor.fetchall()
            conn.commit()
        return [self._row_to_event(row) for row in rows]

    def list_all(self) -> list[AgentEvent]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_events ORDER BY position ASC")
                rows = cursor.fetchall()
            conn.commit()
        return [self._row_to_event(row) for row in rows]

    def get_by_idempotency_key(self, idempotency_key: str) -> AgentEvent | None:
        if not idempotency_key:
            return None
        with self._connect() as conn:
            with conn.cursor() as cursor:
                event = self._get_by_idempotency_key(cursor, idempotency_key)
            conn.commit()
        return event

    def health(self) -> dict[str, Any]:
        try:
            self.config.require_password()
            self.config.connect_kwargs()
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT version, checksum
                        FROM event_store_schema_migrations
                        ORDER BY version ASC
                        """
                    )
                    migrations = [
                        {"version": int(row["version"]), "checksum": str(row["checksum"])}
                        for row in cursor.fetchall()
                    ]
                    cursor.execute(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = 'agent_events'
                        """
                    )
                    columns = {str(row["column_name"]) for row in cursor.fetchall()}
                conn.commit()
            latest_schema_version = max(
                (migration["version"] for migration in migrations),
                default=0,
            )
            missing_columns = [
                column for column in POSTGRES_REQUIRED_COLUMNS if column not in columns
            ]
            return {
                "backend": "postgres",
                "ok": latest_schema_version >= POSTGRES_EXPECTED_SCHEMA_VERSION and not missing_columns,
                "initialized": bool(columns),
                "expected_schema_version": POSTGRES_EXPECTED_SCHEMA_VERSION,
                "latest_schema_version": latest_schema_version,
                "applied_migrations": migrations,
                "missing_columns": missing_columns,
                "host": self.config.host,
                "database": self.config.database,
            }
        except Exception as exc:
            return {
                "backend": "postgres",
                "ok": False,
                "initialized": False,
                "expected_schema_version": POSTGRES_EXPECTED_SCHEMA_VERSION,
                "latest_schema_version": 0,
                "applied_migrations": [],
                "missing_columns": POSTGRES_REQUIRED_COLUMNS,
                "host": self.config.host,
                "database": self.config.database,
                "error": str(exc),
            }

    def _connect(self):
        self.config.require_password()
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ModuleNotFoundError as exc:
            raise PostgresDriverError("Install psycopg to use PostgresEventStore") from exc
        return psycopg.connect(**self.config.connect_kwargs(), row_factory=dict_row)

    def _get_by_idempotency_key(self, cursor, idempotency_key: str) -> AgentEvent | None:
        cursor.execute(
            "SELECT * FROM agent_events WHERE idempotency_key = %s",
            (idempotency_key,),
        )
        row = cursor.fetchone()
        return self._row_to_event(row) if row is not None else None

    @staticmethod
    def _next_sequence(cursor, stream_id: str) -> int:
        cursor.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM agent_events WHERE stream_id = %s",
            (stream_id,),
        )
        return int(cursor.fetchone()["next_sequence"])

    @staticmethod
    def _insert_event(cursor, event: AgentEvent) -> bool:
        cursor.execute(
            """
            INSERT INTO agent_events (
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
                payload_jsonb,
                artifact_refs_jsonb,
                created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s
            )
            ON CONFLICT (idempotency_key) WHERE idempotency_key != ''
            DO NOTHING
            RETURNING event_id
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
        return cursor.fetchone() is not None

    @staticmethod
    def _row_to_event(row: Mapping[str, Any]) -> AgentEvent:
        payload = row.get("payload_jsonb") or {}
        artifact_refs = row.get("artifact_refs_jsonb") or []
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(artifact_refs, str):
            artifact_refs = json.loads(artifact_refs)
        return AgentEvent(
            event_id=row["event_id"],
            stream_id=row["stream_id"],
            sequence=row["sequence"],
            type=row["type"],
            crew_id=row.get("crew_id", ""),
            worker_id=row.get("worker_id", ""),
            turn_id=row.get("turn_id", ""),
            round_id=row.get("round_id", ""),
            contract_id=row.get("contract_id", ""),
            idempotency_key=row.get("idempotency_key", ""),
            payload=payload,
            artifact_refs=artifact_refs,
            created_at=row.get("created_at", ""),
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS event_store_schema_migrations (
        version INTEGER PRIMARY KEY,
        checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_events (
        position BIGSERIAL NOT NULL,
        event_id TEXT PRIMARY KEY,
        stream_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        type TEXT NOT NULL,
        crew_id TEXT NOT NULL DEFAULT '',
        worker_id TEXT NOT NULL DEFAULT '',
        turn_id TEXT NOT NULL DEFAULT '',
        idempotency_key TEXT NOT NULL DEFAULT '',
        payload_jsonb JSONB NOT NULL,
        artifact_refs_jsonb JSONB NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (stream_id, sequence)
    )
    """,
    "ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS position BIGSERIAL",
    "ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS round_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS contract_id TEXT NOT NULL DEFAULT ''",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_events_idempotency_key_non_empty
    ON agent_events (idempotency_key)
    WHERE idempotency_key != ''
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_events_crew_id ON agent_events (crew_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_events_worker_id ON agent_events (worker_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_events_turn_id ON agent_events (turn_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_events_round_id ON agent_events (round_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_events_contract_id ON agent_events (contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_events_created_at ON agent_events (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_agent_events_position ON agent_events (position)",
    """
    INSERT INTO event_store_schema_migrations (version, checksum, applied_at)
    VALUES (1, 'agent_events_v1', CURRENT_TIMESTAMP)
    ON CONFLICT (version) DO NOTHING
    """,
    """
    INSERT INTO event_store_schema_migrations (version, checksum, applied_at)
    VALUES (2, 'agent_events_round_contract_v2', CURRENT_TIMESTAMP)
    ON CONFLICT (version) DO NOTHING
    """,
]
