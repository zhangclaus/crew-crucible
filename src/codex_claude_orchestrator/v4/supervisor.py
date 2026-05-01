"""Facade for running V4 source turns through workflow, delivery, and completion."""

from __future__ import annotations

from codex_claude_orchestrator.v4.artifacts import ArtifactStore
from codex_claude_orchestrator.v4.completion import CompletionDetector
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
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
        )

        self._turns.request_and_deliver(turn)
        runtime_events = [
            self._current_turn_event(turn, runtime_event)
            for runtime_event in self._adapter.watch_turn(turn)
        ]
        for index, runtime_event in enumerate(runtime_events):
            self._events.append(
                stream_id=crew_id,
                type=runtime_event.type,
                crew_id=crew_id,
                worker_id=runtime_event.worker_id,
                turn_id=turn.turn_id,
                idempotency_key=f"{crew_id}/{turn.turn_id}/{runtime_event.type}/{index}",
                payload=runtime_event.payload,
                artifact_refs=runtime_event.artifact_refs,
            )

        decision = self._completion.evaluate(turn, runtime_events)
        self._events.append(
            stream_id=crew_id,
            type=decision.event_type,
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=turn.turn_id,
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
    def _current_turn_event(turn: TurnEnvelope, event: RuntimeEvent) -> RuntimeEvent:
        if event.turn_id == turn.turn_id and event.worker_id == turn.worker_id:
            return event

        return RuntimeEvent(
            type=event.type,
            turn_id=turn.turn_id,
            worker_id=turn.worker_id,
            payload=event.payload,
            artifact_refs=event.artifact_refs,
        )
