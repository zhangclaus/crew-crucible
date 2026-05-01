from __future__ import annotations

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.events import AgentEvent


class V4WorkflowEngine:
    def __init__(self, *, event_store: SQLiteEventStore):
        self._events = event_store

    def start_crew(self, *, crew_id: str, goal: str) -> AgentEvent:
        return self._events.append(
            stream_id=crew_id,
            type="crew.started",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/crew.started",
            payload={"goal": goal},
        )

    def require_human(
        self,
        *,
        crew_id: str,
        reason: str,
        evidence_refs: list[str] | None = None,
    ) -> AgentEvent:
        return self._events.append(
            stream_id=crew_id,
            type="human.required",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/human.required/{reason}",
            payload={"reason": reason},
            artifact_refs=evidence_refs or [],
        )

    def mark_ready(self, *, crew_id: str, round_id: str, evidence_refs: list[str]) -> AgentEvent:
        return self._events.append(
            stream_id=crew_id,
            type="crew.ready_for_accept",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/{round_id}/ready",
            payload={"round_id": round_id},
            artifact_refs=evidence_refs,
        )
