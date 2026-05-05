"""Shared event store protocol for V4 runtimes."""

from __future__ import annotations

from typing import Any, Protocol

from codex_claude_orchestrator.v4.events import AgentEvent


class EventStore(Protocol):
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
        ...

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
        ...

    def list_stream(self, stream_id: str, after_sequence: int = 0) -> list[AgentEvent]:
        ...

    def list_by_turn(self, turn_id: str) -> list[AgentEvent]:
        ...

    def list_all(self) -> list[AgentEvent]:
        ...

    def get_by_idempotency_key(self, idempotency_key: str) -> AgentEvent | None:
        ...

    def health(self) -> dict[str, Any]:
        ...
