from __future__ import annotations

from pathlib import Path

import pytest

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.event_store_factory import (
    EmptyEventStore,
    build_v4_event_store,
)


def test_event_store_factory_returns_empty_readonly_store_when_unconfigured(
    tmp_path: Path,
) -> None:
    store = build_v4_event_store(tmp_path, readonly=True, environ={})

    assert isinstance(store, EmptyEventStore)
    assert store.list_stream("crew-1") == []
    assert store.list_all() == []
    assert not (tmp_path / ".orchestrator").exists()


def test_event_store_factory_reads_explicit_legacy_sqlite_store(tmp_path: Path) -> None:
    store_path = tmp_path / ".orchestrator" / "v4" / "events.sqlite3"
    sqlite_store = SQLiteEventStore(store_path)
    sqlite_store.append(
        stream_id="crew-1",
        type="crew.started",
        crew_id="crew-1",
        payload={"goal": "Fix tests"},
    )

    store = build_v4_event_store(
        tmp_path,
        readonly=True,
        environ={"V4_EVENT_STORE_BACKEND": "sqlite"},
    )

    assert [event.type for event in store.list_stream("crew-1")] == ["crew.started"]


def test_event_store_factory_creates_explicit_writable_sqlite_store(tmp_path: Path) -> None:
    store = build_v4_event_store(
        tmp_path,
        readonly=False,
        environ={"V4_EVENT_STORE_BACKEND": "sqlite"},
    )

    store.append(stream_id="crew-1", type="crew.started", crew_id="crew-1")

    assert (tmp_path / ".orchestrator" / "v4" / "events.sqlite3").exists()
    assert [event.type for event in store.list_stream("crew-1")] == ["crew.started"]


def test_event_store_factory_unknown_backend_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported V4 event store backend"):
        build_v4_event_store(
            tmp_path,
            readonly=True,
            environ={"V4_EVENT_STORE_BACKEND": "redis"},
        )


def test_event_store_factory_postgres_backend_raises_removed_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Postgres event store backend has been removed"):
        build_v4_event_store(
            tmp_path,
            readonly=True,
            environ={"V4_EVENT_STORE_BACKEND": "postgres"},
        )


def test_event_store_factory_pg_backend_raises_removed_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Postgres event store backend has been removed"):
        build_v4_event_store(
            tmp_path,
            readonly=True,
            environ={"V4_EVENT_STORE_BACKEND": "pg"},
        )
