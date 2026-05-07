"""Tests for DomainEventEmitter."""

from __future__ import annotations

import json
from typing import Any
from dataclasses import dataclass, field

import pytest

from codex_claude_orchestrator.v4.domain_events import DomainEventEmitter, _summary_hash
from codex_claude_orchestrator.v4.events import AgentEvent, normalize


# ---------------------------------------------------------------------------
# Mock EventStore
# ---------------------------------------------------------------------------


@dataclass
class MockEventStore:
    """Minimal in-memory mock for testing DomainEventEmitter."""

    _events: list[AgentEvent] = field(default_factory=list)
    _next_seq: int = 1

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
        event = AgentEvent(
            event_id=f"evt-{self._next_seq}",
            stream_id=stream_id,
            sequence=self._next_seq,
            type=type,
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=turn_id,
            round_id=round_id,
            contract_id=contract_id,
            idempotency_key=idempotency_key,
            payload=payload or {},
            artifact_refs=artifact_refs or [],
            created_at=created_at or "2026-05-06T00:00:00Z",
        )
        self._next_seq += 1
        self._events.append(event)
        return event


@pytest.fixture()
def store() -> MockEventStore:
    return MockEventStore()


@pytest.fixture()
def emitter(store: MockEventStore) -> DomainEventEmitter:
    return DomainEventEmitter(store)


# ---------------------------------------------------------------------------
# Crew lifecycle events
# ---------------------------------------------------------------------------


class TestEmitCrewStarted:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter, store: MockEventStore) -> None:
        event = emitter.emit_crew_started("crew-1", "fix the bug", repo="github.com/org/repo")

        assert event.type == "crew.started"
        assert event.crew_id == "crew-1"
        assert event.stream_id == "crew-1"
        assert event.idempotency_key == "crew-1/crew.started"
        assert event.payload == {"goal": "fix the bug", "repo": "github.com/org/repo"}

    def test_includes_extra_kwargs_in_payload(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_crew_started("crew-1", "goal", max_workers=5)

        assert event.payload["max_workers"] == 5
        assert event.payload["goal"] == "goal"

    def test_omits_repo_when_empty(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_crew_started("crew-1", "goal")

        assert "repo" not in event.payload


class TestEmitCrewUpdated:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        updates = {"status": "running", "planner_summary": "ready"}
        event = emitter.emit_crew_updated("crew-1", updates)
        expected_hash = _summary_hash(json.dumps(updates, sort_keys=True, default=str))

        assert event.type == "crew.updated"
        assert event.crew_id == "crew-1"
        assert event.stream_id == "crew-1"
        assert event.idempotency_key == f"crew-1/crew.updated/{expected_hash}"
        assert event.payload == {"status": "running", "planner_summary": "ready"}


class TestEmitCrewStopped:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_crew_stopped("crew-1", reason="timeout")

        assert event.type == "crew.stopped"
        assert event.crew_id == "crew-1"
        assert event.idempotency_key == "crew-1/crew.stopped"
        assert event.payload == {"reason": "timeout"}

    def test_omits_reason_when_empty(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_crew_stopped("crew-1")

        assert event.payload == {}


class TestEmitCrewFinalized:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_crew_finalized("crew-1", "accepted", final_summary="all good")

        assert event.type == "crew.finalized"
        assert event.crew_id == "crew-1"
        assert event.idempotency_key == "crew-1/crew.finalized"
        assert event.payload == {"status": "accepted", "final_summary": "all good"}

    def test_omits_summary_when_empty(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_crew_finalized("crew-1", "failed")

        assert event.payload == {"status": "failed"}
        assert "final_summary" not in event.payload


# ---------------------------------------------------------------------------
# Worker lifecycle events
# ---------------------------------------------------------------------------


class TestEmitWorkerSpawned:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_worker_spawned(
            "crew-1", "worker-1", role="implementer", workspace_path="/tmp/ws"
        )

        assert event.type == "worker.spawned"
        assert event.crew_id == "crew-1"
        assert event.worker_id == "worker-1"
        assert event.stream_id == "crew-1"
        assert event.idempotency_key == "crew-1/worker.spawned/worker-1"
        assert event.payload == {"role": "implementer", "workspace_path": "/tmp/ws"}

    def test_includes_extra_kwargs(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_worker_spawned("crew-1", "w-1", role="reviewer", capabilities=["code-review"])

        assert event.payload["capabilities"] == ["code-review"]


class TestEmitWorkerContractRecorded:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_worker_contract_recorded(
            "crew-1", "contract-1", label="fix-it", mission="fix the auth bug"
        )

        assert event.type == "worker.contract.recorded"
        assert event.crew_id == "crew-1"
        assert event.contract_id == "contract-1"
        assert event.idempotency_key == "crew-1/worker.contract.recorded/contract-1"
        assert event.payload == {"label": "fix-it", "mission": "fix the auth bug"}


class TestEmitWorkerClaimed:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_worker_claimed("crew-1", "worker-1")

        assert event.type == "worker.claimed"
        assert event.crew_id == "crew-1"
        assert event.worker_id == "worker-1"
        assert event.idempotency_key == "crew-1/worker.claimed/worker-1"
        assert event.payload == {}


class TestEmitWorkerReleased:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_worker_released("crew-1", "worker-1")

        assert event.type == "worker.released"
        assert event.crew_id == "crew-1"
        assert event.worker_id == "worker-1"
        assert event.idempotency_key == "crew-1/worker.released/worker-1"
        assert event.payload == {}


class TestEmitWorkerStopped:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_worker_stopped("crew-1", "worker-1")

        assert event.type == "worker.stopped"
        assert event.crew_id == "crew-1"
        assert event.worker_id == "worker-1"
        assert event.idempotency_key == "crew-1/worker.stopped/worker-1"
        assert event.payload == {}


# ---------------------------------------------------------------------------
# Blackboard events
# ---------------------------------------------------------------------------


class TestEmitBlackboardEntry:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_blackboard_entry(
            "crew-1", "entry-1", entry_type="fact", content="the API returns 403"
        )

        assert event.type == "blackboard.entry"
        assert event.crew_id == "crew-1"
        assert event.stream_id == "crew-1"
        assert event.idempotency_key == "crew-1/blackboard/entry-1"
        assert event.payload == {"entry_type": "fact", "content": "the API returns 403"}

    def test_includes_extra_kwargs(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_blackboard_entry(
            "crew-1", "entry-1", entry_type="risk", confidence=0.85
        )

        assert event.payload["confidence"] == 0.85


# ---------------------------------------------------------------------------
# Decision events
# ---------------------------------------------------------------------------


class TestEmitDecisionRecorded:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_decision_recorded(
            "crew-1", "action-1", action_type="spawn_worker", reason="need implementer"
        )

        assert event.type == "decision.recorded"
        assert event.crew_id == "crew-1"
        assert event.stream_id == "crew-1"
        assert event.idempotency_key == "crew-1/decision/action-1"
        assert event.payload == {"action_type": "spawn_worker", "reason": "need implementer"}

    def test_includes_extra_kwargs(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_decision_recorded(
            "crew-1", "action-1", priority=10, worker_id="worker-1"
        )

        assert event.payload["priority"] == 10
        assert event.payload["worker_id"] == "worker-1"


# ---------------------------------------------------------------------------
# Task events
# ---------------------------------------------------------------------------


class TestEmitTaskCreated:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_task_created("crew-1", "task-1", title="Write unit tests")

        assert event.type == "task.created"
        assert event.crew_id == "crew-1"
        assert event.stream_id == "crew-1"
        assert event.idempotency_key == "crew-1/task.created/task-1"
        assert event.payload == {"title": "Write unit tests"}

    def test_includes_extra_kwargs(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_task_created(
            "crew-1", "task-1", title="test", role_required="implementer"
        )

        assert event.payload["role_required"] == "implementer"


# ---------------------------------------------------------------------------
# Artifact events
# ---------------------------------------------------------------------------


class TestEmitArtifactWritten:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_artifact_written(
            "crew-1", "result.json", sha256="abc123def456"
        )

        assert event.type == "artifact.written"
        assert event.crew_id == "crew-1"
        assert event.stream_id == "crew-1"
        assert event.idempotency_key == "crew-1/artifact/result.json/abc123def456"
        assert event.payload == {"artifact_name": "result.json", "sha256": "abc123def456"}

    def test_omits_sha256_when_empty(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_artifact_written("crew-1", "output.txt")

        assert event.payload == {"artifact_name": "output.txt"}
        assert event.idempotency_key == "crew-1/artifact/output.txt/"


# ---------------------------------------------------------------------------
# Pitfall events
# ---------------------------------------------------------------------------


class TestEmitPitfallRecorded:
    def test_produces_correct_event_type(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_pitfall_recorded(
            "crew-1", "auth_failure", summary="403 on protected route", guardrail="check auth middleware"
        )

        expected_hash = _summary_hash("403 on protected route")
        assert event.type == "pitfall.recorded"
        assert event.crew_id == "crew-1"
        assert event.stream_id == "crew-1"
        assert event.idempotency_key == f"crew-1/pitfall/auth_failure/{expected_hash}"
        assert event.payload == {
            "failure_class": "auth_failure",
            "summary": "403 on protected route",
            "guardrail": "check auth middleware",
        }

    def test_omits_optional_fields_when_empty(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_pitfall_recorded("crew-1", "timeout")

        assert event.payload == {"failure_class": "timeout"}
        assert event.idempotency_key == "crew-1/pitfall/timeout/"

    def test_includes_extra_kwargs(self, emitter: DomainEventEmitter) -> None:
        event = emitter.emit_pitfall_recorded(
            "crew-1", "race_condition", evidence_refs=["ref-1", "ref-2"]
        )

        assert event.payload["evidence_refs"] == ["ref-1", "ref-2"]


# ---------------------------------------------------------------------------
# Idempotency key conventions
# ---------------------------------------------------------------------------


class TestIdempotencyKeyConventions:
    """All emit methods follow the {crew_id}/{event_type}/{entity_id} convention."""

    def test_crew_events_use_crew_id_only(self, emitter: DomainEventEmitter) -> None:
        assert emitter.emit_crew_started("c1", "g").idempotency_key == "c1/crew.started"
        assert emitter.emit_crew_stopped("c1").idempotency_key == "c1/crew.stopped"
        assert emitter.emit_crew_finalized("c1", "ok").idempotency_key == "c1/crew.finalized"

    def test_crew_updated_includes_payload_hash(self, emitter: DomainEventEmitter) -> None:
        updates = {"status": "done"}
        key = emitter.emit_crew_updated("c1", updates).idempotency_key
        expected_hash = _summary_hash(json.dumps(updates, sort_keys=True, default=str))
        assert key == f"c1/crew.updated/{expected_hash}"

    def test_worker_events_include_worker_id(self, emitter: DomainEventEmitter) -> None:
        assert emitter.emit_worker_spawned("c1", "w1").idempotency_key == "c1/worker.spawned/w1"
        assert emitter.emit_worker_claimed("c1", "w1").idempotency_key == "c1/worker.claimed/w1"
        assert emitter.emit_worker_released("c1", "w1").idempotency_key == "c1/worker.released/w1"
        assert emitter.emit_worker_stopped("c1", "w1").idempotency_key == "c1/worker.stopped/w1"

    def test_contract_events_include_contract_id(self, emitter: DomainEventEmitter) -> None:
        key = emitter.emit_worker_contract_recorded("c1", "ct1").idempotency_key
        assert key == "c1/worker.contract.recorded/ct1"

    def test_blackboard_events_include_entry_id(self, emitter: DomainEventEmitter) -> None:
        assert emitter.emit_blackboard_entry("c1", "e1").idempotency_key == "c1/blackboard/e1"

    def test_decision_events_include_action_id(self, emitter: DomainEventEmitter) -> None:
        assert emitter.emit_decision_recorded("c1", "a1").idempotency_key == "c1/decision/a1"

    def test_task_events_include_task_id(self, emitter: DomainEventEmitter) -> None:
        assert emitter.emit_task_created("c1", "t1").idempotency_key == "c1/task.created/t1"

    def test_artifact_events_include_name_and_sha(self, emitter: DomainEventEmitter) -> None:
        assert emitter.emit_artifact_written("c1", "out.json", "sha1").idempotency_key == "c1/artifact/out.json/sha1"

    def test_pitfall_events_include_failure_class_and_summary_hash(self, emitter: DomainEventEmitter) -> None:
        key = emitter.emit_pitfall_recorded("c1", "timeout", summary="too slow").idempotency_key
        assert key == f"c1/pitfall/timeout/{_summary_hash('too slow')}"


# ---------------------------------------------------------------------------
# EventStore.append() parameter forwarding
# ---------------------------------------------------------------------------


class TestAppendParameters:
    """Verify that each emit method passes correct parameters to EventStore.append()."""

    def test_crew_started_passes_stream_id_and_type(self, emitter: DomainEventEmitter, store: MockEventStore) -> None:
        emitter.emit_crew_started("crew-1", "goal")
        event = store._events[-1]

        assert event.stream_id == "crew-1"
        assert event.type == "crew.started"
        assert event.crew_id == "crew-1"
        assert event.worker_id == ""
        assert event.contract_id == ""

    def test_worker_spawned_passes_worker_id(self, emitter: DomainEventEmitter, store: MockEventStore) -> None:
        emitter.emit_worker_spawned("crew-1", "w-1", role="reviewer")
        event = store._events[-1]

        assert event.stream_id == "crew-1"
        assert event.type == "worker.spawned"
        assert event.crew_id == "crew-1"
        assert event.worker_id == "w-1"

    def test_contract_recorded_passes_contract_id(self, emitter: DomainEventEmitter, store: MockEventStore) -> None:
        emitter.emit_worker_contract_recorded("crew-1", "ct-1", label="test")
        event = store._events[-1]

        assert event.contract_id == "ct-1"
        assert event.worker_id == ""

    def test_payload_is_normalized(self, emitter: DomainEventEmitter, store: MockEventStore) -> None:
        """Payload with dataclass values should be normalized to plain dicts."""
        from pathlib import Path

        emitter.emit_crew_started("crew-1", "goal", repo=Path("/tmp/repo"))
        event = store._events[-1]

        assert event.payload["repo"] == "/tmp/repo"


# ---------------------------------------------------------------------------
# Summary hash helper
# ---------------------------------------------------------------------------


class TestSummaryHash:
    def test_returns_consistent_hash(self) -> None:
        assert _summary_hash("hello") == _summary_hash("hello")

    def test_different_inputs_produce_different_hashes(self) -> None:
        assert _summary_hash("hello") != _summary_hash("world")

    def test_returns_hex_string_of_expected_length(self) -> None:
        result = _summary_hash("test input")
        assert len(result) == 12
        int(result, 16)  # should not raise
