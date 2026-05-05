"""Facade for running V4 source turns through workflow, delivery, and completion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.v4.artifacts import ArtifactStore
from codex_claude_orchestrator.v4.completion import CompletionDetector
from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent, normalize
from codex_claude_orchestrator.v4.paths import V4Paths
from codex_claude_orchestrator.v4.runtime import (
    RuntimeAdapter,
    RuntimeEvent,
    TurnEnvelope,
    WorkerHandle,
    WorkerSpec,
)
from codex_claude_orchestrator.v4.turns import TurnService
from codex_claude_orchestrator.v4.workflow import V4WorkflowEngine


class V4Supervisor:
    def __init__(
        self,
        *,
        event_store: EventStore,
        artifact_store: ArtifactStore,
        adapter: RuntimeAdapter,
        turn_context_builder=None,
        adversarial_evaluator=None,
        message_ack_processor=None,
        repo_root: str | Path | None = None,
    ) -> None:
        self._events = event_store
        self._artifacts = artifact_store
        self._adapter = adapter
        self._turn_context_builder = turn_context_builder
        self._adversarial_evaluator = adversarial_evaluator
        self._message_ack_processor = message_ack_processor
        self._repo_root = Path(repo_root).resolve() if repo_root is not None else None
        self._turns = TurnService(event_store=event_store, adapter=adapter)
        self._workflow = V4WorkflowEngine(event_store=event_store)
        self._completion = CompletionDetector()

    def register_worker(self, spec: WorkerSpec) -> WorkerHandle:
        return self._adapter.spawn_worker(spec)

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
        return self.run_worker_turn(
            crew_id=crew_id,
            goal=goal,
            worker_id=worker_id,
            round_id=round_id,
            phase="source",
            contract_id="source_write",
            message=message,
            expected_marker=expected_marker,
        )

    def run_worker_turn(
        self,
        *,
        crew_id: str,
        goal: str,
        worker_id: str,
        round_id: str,
        phase: str,
        contract_id: str,
        message: str,
        expected_marker: str,
    ) -> dict[str, str]:
        self._workflow.start_crew(crew_id=crew_id, goal=goal)
        context = self._build_turn_context(crew_id=crew_id, worker_id=worker_id)
        turn_id = f"{round_id}-{worker_id}-{phase}"
        required_outbox_path = self._prepare_required_outbox_path(
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=turn_id,
        )
        turn = TurnEnvelope(
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=turn_id,
            round_id=round_id,
            phase=phase,
            message=message,
            expected_marker=expected_marker,
            required_outbox_path=required_outbox_path,
            contract_id=contract_id,
            unread_inbox_digest=context.get("unread_inbox_digest", ""),
            unread_message_ids=context.get("unread_message_ids", []),
            open_protocol_requests=context.get("open_protocol_requests", []),
            open_protocol_requests_digest=context.get("open_protocol_requests_digest", ""),
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
            event_payload = _runtime_event_payload_for_storage(runtime_event)
            event = self._events.append(
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
                payload=event_payload,
                artifact_refs=runtime_event.artifact_refs,
            )
            self._process_message_ack_if_configured(event)
        self._commit_runtime_events_if_supported(turn, runtime_events)

        terminal_result = self._terminal_result(crew_id=crew_id, turn=turn)
        if terminal_result is not None:
            return terminal_result

        decision = self._completion.evaluate(turn, runtime_events)
        terminal_event = self._events.append(
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
        self._evaluate_completed_turn_if_configured(terminal_event)
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

    def _build_turn_context(self, *, crew_id: str, worker_id: str) -> dict:
        if self._turn_context_builder is None:
            return {}
        context = self._turn_context_builder.build(crew_id=crew_id, worker_id=worker_id)
        return {
            "unread_inbox_digest": getattr(context, "unread_inbox_digest", ""),
            "unread_message_ids": list(getattr(context, "unread_message_ids", [])),
            "open_protocol_requests": list(getattr(context, "open_protocol_requests", [])),
            "open_protocol_requests_digest": getattr(context, "open_protocol_requests_digest", ""),
        }

    def _prepare_required_outbox_path(
        self,
        *,
        crew_id: str,
        worker_id: str,
        turn_id: str,
    ) -> str:
        outbox_path = self._paths_for(crew_id).outbox_path(worker_id, turn_id)
        outbox_path.parent.mkdir(parents=True, exist_ok=True)
        return str(outbox_path)

    def _paths_for(self, crew_id: str) -> V4Paths:
        repo_root = self._repo_root
        if repo_root is None:
            repo_root = _infer_repo_root_from_artifact_root(
                self._artifacts.root,
                crew_id=crew_id,
            )
        return V4Paths(repo_root=repo_root, crew_id=crew_id)

    def _terminal_result(self, *, crew_id: str, turn: TurnEnvelope) -> dict[str, str] | None:
        for event in reversed(self._events.list_by_turn(turn.turn_id)):
            if event.crew_id != crew_id:
                continue
            if event.type == "turn.completed":
                self._evaluate_completed_turn_if_configured(event)
                return {
                    "crew_id": crew_id,
                    "status": "turn_completed",
                    "turn_id": turn.turn_id,
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

    def _evaluate_completed_turn_if_configured(self, event: AgentEvent) -> None:
        if event.type == "turn.completed" and self._adversarial_evaluator is not None:
            self._adversarial_evaluator.evaluate_completed_turn(event)

    def _process_message_ack_if_configured(self, event: AgentEvent) -> None:
        if self._message_ack_processor is not None:
            self._message_ack_processor.process(event)

    def _commit_runtime_events_if_supported(
        self,
        turn: TurnEnvelope,
        runtime_events: list[RuntimeEvent],
    ) -> None:
        commit = getattr(self._adapter, "commit_runtime_events", None)
        if callable(commit):
            try:
                commit(turn, runtime_events)
            except Exception as exc:
                self._events.append(
                    stream_id=turn.crew_id,
                    type="runtime.stream_commit_failed",
                    crew_id=turn.crew_id,
                    worker_id=turn.worker_id,
                    turn_id=turn.turn_id,
                    round_id=turn.round_id,
                    contract_id=turn.contract_id,
                    idempotency_key=f"{turn.crew_id}/{turn.turn_id}/runtime.stream_commit_failed",
                    payload={"error": str(exc)},
                )


def _runtime_event_digest(event: RuntimeEvent, *, index: int) -> str:
    content: dict[str, Any] = {
        "index": index,
        "type": event.type,
        "payload": _runtime_event_payload_for_storage(event),
        "artifact_refs": event.artifact_refs,
    }
    encoded = json.dumps(
        normalize(content),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _runtime_event_payload_for_storage(event: RuntimeEvent) -> dict[str, Any]:
    return {
        key: value
        for key, value in event.payload.items()
        if key != "_stream_state"
    }


def _infer_repo_root_from_artifact_root(artifact_root: Path, *, crew_id: str) -> Path:
    resolved = artifact_root.resolve()
    parts = resolved.parts
    suffix = (".orchestrator", "crews", crew_id, "artifacts", "v4")
    if len(parts) >= len(suffix) and tuple(parts[-len(suffix):]) == suffix:
        return Path(*parts[:-len(suffix)])
    return resolved.parent
