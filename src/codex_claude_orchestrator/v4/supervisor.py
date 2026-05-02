"""Facade for running V4 source turns through workflow, delivery, and completion."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from codex_claude_orchestrator.v4.artifacts import ArtifactStore
from codex_claude_orchestrator.v4.completion import CompletionDetector
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.events import normalize
from codex_claude_orchestrator.v4.runtime import RuntimeAdapter, RuntimeEvent, TurnEnvelope
from codex_claude_orchestrator.v4.turns import TurnService
from codex_claude_orchestrator.v4.workflow import V4WorkflowEngine


class V4Supervisor:
    def __init__(
        self,
        *,
        event_store: SQLiteEventStore,
        artifact_store: ArtifactStore,
        adapter: RuntimeAdapter,
    ) -> None:
        self._events = event_store
        self._artifacts = artifact_store
        self._adapter = adapter
        self._turns = TurnService(event_store=event_store, adapter=adapter)
        self._workflow = V4WorkflowEngine(event_store=event_store)
        self._completion = CompletionDetector()

    def run_source_turn(
        self,
        *,
        crew_id: str,
        goal: str,
        worker_id: str,
        round_id: str,
        message: str,
        expected_marker: str,
    ) -> dict[str, str]:
        self._workflow.start_crew(crew_id=crew_id, goal=goal)
        turn = TurnEnvelope(
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=f"{round_id}-{worker_id}-source",
            round_id=round_id,
            phase="source",
            message=message,
            expected_marker=expected_marker,
            contract_id="source_write",
        )

        terminal_result = self._terminal_result(crew_id=crew_id, turn=turn)
        if terminal_result is not None:
            return terminal_result

        delivery_result = self._turns.request_and_deliver(turn)
        terminal_result = self._terminal_result(crew_id=crew_id, turn=turn)
        if terminal_result is not None:
            return terminal_result

        if not delivery_result.delivered:
            status = (
                "waiting"
                if delivery_result.reason == "delivery already in progress"
                else "delivery_failed"
            )
            return {
                "crew_id": crew_id,
                "status": status,
                "turn_id": turn.turn_id,
                "reason": delivery_result.reason,
            }

        runtime_events = [
            runtime_event
            for runtime_event in self._adapter.watch_turn(turn)
            if self._is_current_turn_event(turn, runtime_event)
        ]
        for index, runtime_event in enumerate(runtime_events):
            self._events.append(
                stream_id=crew_id,
                type=runtime_event.type,
                crew_id=crew_id,
                worker_id=runtime_event.worker_id,
                turn_id=runtime_event.turn_id,
                round_id=turn.round_id,
                contract_id=turn.contract_id,
                idempotency_key=(
                    f"{crew_id}/{turn.turn_id}/{runtime_event.type}/{index}/"
                    f"{_runtime_event_digest(runtime_event, index=index)}"
                ),
                payload=runtime_event.payload,
                artifact_refs=runtime_event.artifact_refs,
            )

        terminal_result = self._terminal_result(crew_id=crew_id, turn=turn)
        if terminal_result is not None:
            return terminal_result

        decision = self._completion.evaluate(turn, runtime_events)
        self._events.append(
            stream_id=crew_id,
            type=decision.event_type,
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=turn.turn_id,
            round_id=turn.round_id,
            contract_id=turn.contract_id,
            idempotency_key=f"{crew_id}/{turn.turn_id}/{decision.event_type}",
            payload={"reason": decision.reason},
            artifact_refs=decision.evidence_refs,
        )
        if decision.event_type == "turn.completed":
            return {"crew_id": crew_id, "status": "turn_completed", "turn_id": turn.turn_id}
        return {
            "crew_id": crew_id,
            "status": "waiting",
            "turn_id": turn.turn_id,
            "reason": decision.reason,
        }

    @staticmethod
    def _is_current_turn_event(turn: TurnEnvelope, event: RuntimeEvent) -> bool:
        return event.turn_id == turn.turn_id and event.worker_id == turn.worker_id

    def _terminal_result(self, *, crew_id: str, turn: TurnEnvelope) -> dict[str, str] | None:
        for event in reversed(self._events.list_by_turn(turn.turn_id)):
            if event.crew_id != crew_id:
                continue
            if event.type == "turn.completed":
                return {
                    "crew_id": crew_id,
                    "status": "turn_completed",
                    "turn_id": turn.turn_id,
                }
            if event.type == "turn.inconclusive":
                return {
                    "crew_id": crew_id,
                    "status": "waiting",
                    "turn_id": turn.turn_id,
                    "reason": event.payload.get("reason", ""),
                }
            if event.type == "turn.failed":
                return {
                    "crew_id": crew_id,
                    "status": "turn_failed",
                    "turn_id": turn.turn_id,
                    "reason": event.payload.get("reason", ""),
                }
            if event.type == "turn.timeout":
                return {
                    "crew_id": crew_id,
                    "status": "turn_timeout",
                    "turn_id": turn.turn_id,
                    "reason": event.payload.get("reason", ""),
                }
            if event.type == "turn.cancelled":
                return {
                    "crew_id": crew_id,
                    "status": "turn_cancelled",
                    "turn_id": turn.turn_id,
                    "reason": event.payload.get("reason", ""),
                }
        return None


def _runtime_event_digest(event: RuntimeEvent, *, index: int) -> str:
    content: dict[str, Any] = {
        "index": index,
        "type": event.type,
        "payload": event.payload,
        "artifact_refs": event.artifact_refs,
    }
    encoded = json.dumps(
        normalize(content),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
