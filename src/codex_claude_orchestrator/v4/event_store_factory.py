"""Factory for V4 event-store backends."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent
from codex_claude_orchestrator.v4.postgres_event_store import (
    PostgresConfigurationError,
    PostgresEventStore,
    PostgresEventStoreConfig,
)


class EmptyEventStore:
    """Read-only event store used when no configured backend is available."""

    def append(self, **_: Any) -> AgentEvent:
        raise RuntimeError("empty V4 event store is read-only")

    def append_claim(self, **_: Any) -> tuple[AgentEvent, bool]:
        raise RuntimeError("empty V4 event store is read-only")

    def list_stream(self, stream_id: str, after_sequence: int = 0) -> list[AgentEvent]:
        return []

    def list_by_turn(self, turn_id: str) -> list[AgentEvent]:
        return []

    def list_all(self) -> list[AgentEvent]:
        return []

    def get_by_idempotency_key(self, idempotency_key: str) -> AgentEvent | None:
        return None

    def health(self) -> dict[str, Any]:
        return {
            "backend": "empty",
            "ok": False,
            "initialized": False,
            "expected_schema_version": 0,
            "latest_schema_version": 0,
            "applied_migrations": [],
            "missing_columns": [],
            "error": "no V4 event store backend is available",
        }


def build_v4_event_store(
    repo_root: Path,
    *,
    readonly: bool = False,
    environ: Mapping[str, str] | None = None,
) -> EventStore:
    env = environ or os.environ
    backend = env.get("V4_EVENT_STORE_BACKEND", "auto").strip().lower()
    if backend in {"auto", ""}:
        return _build_auto(repo_root, readonly=readonly, environ=env)
    if backend in {"postgres", "pg"}:
        return _build_postgres(readonly=readonly, environ=env)
    if backend in {"sqlite", "legacy_sqlite", "legacy-sqlite"}:
        return _build_legacy_sqlite(repo_root, readonly=readonly)
    raise ValueError(f"unsupported V4 event store backend: {backend}")


def _build_auto(
    repo_root: Path,
    *,
    readonly: bool,
    environ: Mapping[str, str],
):
    try:
        return _build_postgres(readonly=readonly, environ=environ)
    except PostgresConfigurationError:
        return _build_legacy_sqlite(repo_root, readonly=readonly, empty_when_missing=True)


def _build_postgres(*, readonly: bool, environ: Mapping[str, str]):
    config = PostgresEventStoreConfig.from_env(environ)
    config.connect_kwargs()
    store = PostgresEventStore(config)
    if not readonly:
        store.initialize()
    return store


def _build_legacy_sqlite(
    repo_root: Path,
    *,
    readonly: bool,
    empty_when_missing: bool = True,
):
    path = repo_root.resolve() / ".orchestrator" / "v4" / "events.sqlite3"
    if readonly:
        if not path.exists():
            return EmptyEventStore()
        return SQLiteEventStore.open_existing(path)
    if empty_when_missing and readonly:
        return EmptyEventStore()
    return SQLiteEventStore(path)


__all__ = ["EmptyEventStore", "build_v4_event_store"]
