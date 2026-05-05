from __future__ import annotations

from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent


TERMINAL_TURN_EVENTS = {"turn.completed", "turn.failed", "turn.timeout", "turn.cancelled", "turn.inconclusive"}


class Reconciler:
    def __init__(self, *, event_store: EventStore):
        self._events = event_store

    def reconcile_turn(self, crew_id: str, turn_id: str) -> AgentEvent | None:
        events = [event for event in self._events.list_by_turn(turn_id) if event.crew_id == crew_id]
        if any(event.type in TERMINAL_TURN_EVENTS for event in events):
            return None
        delivered = next((event for event in events if event.type == "turn.delivered"), None)
        if delivered is None:
            return None
        return self._events.append(
            stream_id=crew_id,
            type="turn.inconclusive",
            crew_id=crew_id,
            worker_id=delivered.worker_id,
            turn_id=turn_id,
            idempotency_key=f"{crew_id}/{turn_id}/reconcile/inconclusive",
            payload={"reason": "turn was delivered without completion evidence"},
            artifact_refs=delivered.artifact_refs,
        )
