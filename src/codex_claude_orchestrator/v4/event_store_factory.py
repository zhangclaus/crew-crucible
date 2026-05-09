"""Factory for V4 event-store backends."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent


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
        return _build_legacy_sqlite(repo_root, readonly=readonly)
    if backend in {"postgres", "pg"}:
        raise ValueError(
            "The Postgres event store backend has been removed. "
            "Use V4_EVENT_STORE_BACKEND=sqlite or leave unset for auto."
        )
    if backend in {"sqlite", "legacy_sqlite", "legacy-sqlite"}:
        return _build_legacy_sqlite(repo_root, readonly=readonly)
    raise ValueError(f"unsupported V4 event store backend: {backend}")


def _build_legacy_sqlite(
    repo_root: Path,
    *,
    readonly: bool,
):
    path = repo_root.resolve() / ".orchestrator" / "v4" / "events.sqlite3"
    if readonly:
        if not path.exists():
            return EmptyEventStore()
        return SQLiteEventStore.open_existing(path)
    return SQLiteEventStore(path)


__all__ = ["EmptyEventStore", "build_v4_event_store"]
