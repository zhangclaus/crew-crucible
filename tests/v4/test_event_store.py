from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import sqlite3

import pytest

from codex_claude_orchestrator.v4.events import AgentEvent
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore


class EventType(StrEnum):
    WORKER_STARTED = "worker.started"


@dataclass
class NestedPayload:
    path: Path
    kind: EventType


def test_agent_event_to_dict_normalizes_nested_values() -> None:
    event = AgentEvent(
        event_id="evt-1",
        stream_id="stream-1",
        sequence=1,
        type=EventType.WORKER_STARTED,
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
        idempotency_key="worker-1:start",
        payload={
            "nested": NestedPayload(Path("logs/worker-1.jsonl"), EventType.WORKER_STARTED),
            7: [Path("artifacts/result.txt"), EventType.WORKER_STARTED],
        },
        artifact_refs=["artifact-1"],
        created_at="2026-05-01T00:00:00Z",
    )

    assert event.to_dict() == {
        "event_id": "evt-1",
        "stream_id": "stream-1",
        "sequence": 1,
        "type": "worker.started",
        "crew_id": "crew-1",
        "worker_id": "worker-1",
        "turn_id": "turn-1",
        "round_id": "round-1",
        "contract_id": "contract-1",
        "idempotency_key": "worker-1:start",
        "payload": {
            "nested": {"path": "logs/worker-1.jsonl", "kind": "worker.started"},
            "7": ["artifacts/result.txt", "worker.started"],
        },
        "artifact_refs": ["artifact-1"],
        "created_at": "2026-05-01T00:00:00Z",
    }


def test_agent_event_to_dict_includes_stable_default_values() -> None:
    event = AgentEvent(
        event_id="evt-1",
        stream_id="crew-1",
        sequence=1,
        type="crew.started",
    )

    assert event.to_dict() == {
        "event_id": "evt-1",
        "stream_id": "crew-1",
        "sequence": 1,
        "type": "crew.started",
        "crew_id": "",
        "worker_id": "",
        "turn_id": "",
        "round_id": "",
        "contract_id": "",
        "idempotency_key": "",
        "payload": {},
        "artifact_refs": [],
        "created_at": "",
    }


def test_agent_event_rejects_missing_type() -> None:
    with pytest.raises(ValueError, match="type is required"):
        AgentEvent(event_id="evt-1", stream_id="stream-1", sequence=1, type="")


def test_sqlite_event_store_appends_sequences_per_stream_and_lists_in_order(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    first = store.append(stream_id="crew-1", type="crew.started", payload={"step": 1})
    second = store.append(stream_id="crew-1", type="crew.updated", payload={"step": 2})
    other = store.append(stream_id="worker-1", type="worker.started", payload={"step": 1})

    assert first.sequence == 1
    assert second.sequence == 2
    assert other.sequence == 1
    assert [event.event_id for event in store.list_stream("crew-1")] == [
        first.event_id,
        second.event_id,
    ]


def test_sqlite_event_store_idempotency_key_dedupes_to_original_event(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    first = store.append(
        stream_id="crew-1",
        type="crew.started",
        idempotency_key="crew-1:start",
        payload={"original": True},
    )
    duplicate = store.append(
        stream_id="crew-1",
        type="crew.started",
        idempotency_key="crew-1:start",
        payload={"original": False},
    )

    assert duplicate.event_id == first.event_id
    assert duplicate.payload == {"original": True}
    assert [event.event_id for event in store.list_stream("crew-1")] == [first.event_id]


def test_sqlite_event_store_round_and_contract_fields(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    event = store.append(
        stream_id="crew-1",
        type="turn.requested",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
        idempotency_key="crew-1/turn-1/requested",
    )

    loaded = store.list_by_turn("turn-1")[0]
    assert event.round_id == "round-1"
    assert event.contract_id == "contract-1"
    assert loaded.round_id == "round-1"
    assert loaded.contract_id == "contract-1"


def test_sqlite_event_store_health_reports_schema_version(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    health = store.health()

    assert health["backend"] == "sqlite"
    assert health["ok"] is True
    assert health["initialized"] is True
    assert health["expected_schema_version"] == 2
    assert health["latest_schema_version"] == 2
    assert health["applied_migrations"] == [
        {"version": 1, "checksum": "sqlite_events_v1"},
        {"version": 2, "checksum": "sqlite_events_round_contract_v2"},
    ]
    assert health["missing_columns"] == []


def test_sqlite_event_store_health_detects_old_schema(tmp_path: Path) -> None:
    store_path = tmp_path / "events.db"
    with sqlite3.connect(store_path) as conn:
        conn.execute(
            """
            CREATE TABLE events (
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

    health = SQLiteEventStore.open_existing(store_path).health()

    assert health["ok"] is False
    assert health["initialized"] is True
    assert health["latest_schema_version"] == 0
    assert health["missing_columns"] == ["round_id", "contract_id"]


def test_sqlite_event_store_append_claim_reports_whether_event_was_inserted(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    first, first_inserted = store.append_claim(
        stream_id="crew-1",
        type="turn.delivery_started",
        idempotency_key="crew-1/worker-1/turn-1/source/attempt-1/delivery-started",
        payload={"attempt": 1},
    )
    second, second_inserted = store.append_claim(
        stream_id="crew-1",
        type="turn.delivery_started",
        idempotency_key="crew-1/worker-1/turn-1/source/attempt-1/delivery-started",
        payload={"attempt": 2},
    )

    assert first_inserted is True
    assert second_inserted is False
    assert second.event_id == first.event_id
    assert second.payload == {"attempt": 1}
    assert store.list_stream("crew-1") == [first]


def test_sqlite_event_store_list_stream_filters_after_sequence(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    first = store.append(stream_id="crew-1", type="crew.started")
    second = store.append(stream_id="crew-1", type="crew.updated")
    third = store.append(stream_id="crew-1", type="crew.finished")

    assert [event.event_id for event in store.list_stream("crew-1", after_sequence=1)] == [
        second.event_id,
        third.event_id,
    ]
    assert first.event_id not in [
        event.event_id for event in store.list_stream("crew-1", after_sequence=1)
    ]


def test_sqlite_event_store_open_existing_does_not_initialize_schema(tmp_path: Path) -> None:
    store_path = tmp_path / "events.db"
    store_path.write_bytes(b"")

    store = SQLiteEventStore.open_existing(store_path)

    assert store.list_all() == []
    assert store_path.read_bytes() == b""


def test_sqlite_event_store_open_existing_reads_old_schema_without_round_contract(tmp_path: Path) -> None:
    store_path = tmp_path / "events.db"
    with sqlite3.connect(store_path) as conn:
        conn.execute(
            """
            CREATE TABLE events (
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
            INSERT INTO events (
                event_id, stream_id, sequence, type, crew_id, worker_id, turn_id,
                idempotency_key, payload_json, artifact_refs_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt-1",
                "crew-1",
                1,
                "crew.started",
                "crew-1",
                "",
                "",
                "",
                "{}",
                "[]",
                "2026-05-02T00:00:00Z",
            ),
        )

    store = SQLiteEventStore.open_existing(store_path)
    event = store.list_all()[0]

    assert event.round_id == ""
    assert event.contract_id == ""


def test_event_store_list_by_turn_preserves_append_order_across_streams(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    first = store.append(stream_id="z-stream", type="worker.updated", turn_id="turn-1")
    second = store.append(stream_id="a-stream", type="worker.updated", turn_id="turn-1")

    assert [event.event_id for event in store.list_by_turn("turn-1")] == [
        first.event_id,
        second.event_id,
    ]


def test_event_store_concurrent_idempotent_appends_return_one_event(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")

    def append_event(index: int) -> AgentEvent:
        return store.append(
            stream_id="crew-1",
            type="crew.updated",
            idempotency_key="crew-1:update",
            payload={"index": index},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        events = list(executor.map(append_event, range(16)))

    assert {event.event_id for event in events} == {events[0].event_id}
    assert store.list_stream("crew-1") == [events[0]]


def test_event_store_concurrent_same_stream_appends_get_unique_sequences(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.db")
    event_count = 24

    def append_event(index: int) -> AgentEvent:
        return store.append(
            stream_id="crew-1",
            type="crew.updated",
            idempotency_key=f"crew-1:update:{index}",
            payload={"index": index},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        events = list(executor.map(append_event, range(event_count)))

    assert sorted(event.sequence for event in events) == list(range(1, event_count + 1))
    assert [event.sequence for event in store.list_stream("crew-1")] == list(
        range(1, event_count + 1)
    )
