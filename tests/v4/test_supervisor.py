import json
from pathlib import Path

import pytest

from codex_claude_orchestrator.crew.models import AgentMessageType, CrewRecord
from codex_claude_orchestrator.messaging.message_bus import AgentMessageBus
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.v4.adapters.tmux_claude import ClaudeCodeTmuxAdapter
from codex_claude_orchestrator.v4.artifacts import ArtifactStore
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.message_ack import MessageAckProcessor
from codex_claude_orchestrator.v4.paths import V4Paths
from codex_claude_orchestrator.v4.runtime import DeliveryResult, RuntimeEvent, TurnEnvelope
from codex_claude_orchestrator.v4.supervisor import V4Supervisor
from codex_claude_orchestrator.v4.turn_context import TurnContextBuilder


class FakeAdapter:
    def __init__(self, events=None, delivery_result=None):
        self.events = events or []
        self.delivery_result = delivery_result
        self.delivered = []
        self.delivered_turns = []
        self.watched = []

    def deliver_turn(self, turn: TurnEnvelope):
        self.delivered.append(turn.turn_id)
        self.delivered_turns.append(turn)
        if self.delivery_result is not None:
            return self.delivery_result
        return DeliveryResult(delivered=True, marker=turn.expected_marker, reason="sent")

    def watch_turn(self, turn: TurnEnvelope):
        self.watched.append(turn.turn_id)
        events = self.events(turn) if callable(self.events) else self.events
        return iter(events)


class CommitRecordingAdapter(FakeAdapter):
    def __init__(self, events=None, delivery_result=None):
        super().__init__(events=events, delivery_result=delivery_result)
        self.committed = []

    def commit_runtime_events(self, turn: TurnEnvelope, events: list[RuntimeEvent]) -> None:
        self.committed.append((turn.turn_id, list(events)))


def completed_outbox_event(turn: TurnEnvelope) -> RuntimeEvent:
    return RuntimeEvent(
        type="worker.outbox.detected",
        turn_id=turn.turn_id,
        worker_id=turn.worker_id,
        payload={"valid": True, "status": "completed"},
        artifact_refs=[f"workers/{turn.worker_id}/outbox/{turn.turn_id}.json"],
    )


class FakeTurnContextBuilder:
    def build(self, *, crew_id: str, worker_id: str):
        return type(
            "FakeTurnContext",
            (),
            {
                "unread_inbox_digest": "unread: review this",
                "unread_message_ids": ["msg-1"],
                "open_protocol_requests": [{"request_id": "req-1", "subject": "Review"}],
                "open_protocol_requests_digest": "open: Review",
            },
        )()


class FakeAdversarialEvaluator:
    def __init__(self):
        self.completed_events = []

    def evaluate_completed_turn(self, completed_event):
        self.completed_events.append(completed_event)
        return completed_event


class FlakyAdversarialEvaluator:
    def __init__(self):
        self.completed_events = []

    def evaluate_completed_turn(self, completed_event):
        self.completed_events.append(completed_event)
        if len(self.completed_events) == 1:
            raise RuntimeError("evaluation failed")
        return completed_event


class FakeNativeSession:
    def __init__(self):
        self.sent = []
        self.observe_result = {
            "snapshot": "",
            "marker": "marker-1",
            "marker_seen": False,
            "transcript_artifact": "",
        }

    def send(self, **kwargs):
        self.sent.append(kwargs)
        return {
            "marker": kwargs["turn_marker"],
            "message": kwargs["message"],
        }

    def observe(self, **kwargs):
        return self.observe_result


def test_v4_supervisor_runs_until_turn_completed(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter(
            lambda turn: [
                completed_outbox_event(turn),
            ]
        ),
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "turn_completed"
    assert [event.type for event in store.list_stream("crew-1")] == [
        "crew.started",
        "turn.requested",
        "turn.delivery_started",
        "turn.delivered",
        "worker.outbox.detected",
        "turn.completed",
    ]


def test_v4_supervisor_commits_runtime_stream_state_after_event_append(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")

    def events_for_turn(turn: TurnEnvelope):
        return [
            RuntimeEvent(
                type="worker.outbox.detected",
                turn_id=turn.turn_id,
                worker_id=turn.worker_id,
                payload={
                    "valid": True,
                    "status": "completed",
                    "_stream_state": {
                        "kind": "outbox",
                        "key": "turn-1:/tmp/outbox.json",
                        "sha256": "abc",
                    },
                },
            )
        ]

    adapter = CommitRecordingAdapter(events_for_turn)
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    outbox_event = [
        event for event in store.list_stream("crew-1") if event.type == "worker.outbox.detected"
    ][0]
    assert result["status"] == "turn_completed"
    assert adapter.committed[0][0] == "round-1-worker-1-source"
    assert "_stream_state" not in outbox_event.payload


def test_v4_supervisor_invokes_adversarial_evaluator_after_turn_completed(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    evaluator = FakeAdversarialEvaluator()
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter(lambda turn: [completed_outbox_event(turn)]),
        adversarial_evaluator=evaluator,
    )

    supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert [event.type for event in evaluator.completed_events] == ["turn.completed"]


def test_v4_supervisor_retries_adversarial_evaluator_for_existing_completed_turn(
    tmp_path: Path,
):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    evaluator = FlakyAdversarialEvaluator()
    adapter = FakeAdapter(lambda turn: [completed_outbox_event(turn)])
    first_supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
        adversarial_evaluator=evaluator,
    )

    with pytest.raises(RuntimeError, match="evaluation failed"):
        first_supervisor.run_source_turn(
            crew_id="crew-1",
            goal="Fix tests",
            worker_id="worker-1",
            round_id="round-1",
            message="Implement",
            expected_marker="marker-1",
        )

    resumed_supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
        adversarial_evaluator=evaluator,
    )
    result = resumed_supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "turn_completed"
    assert [event.type for event in evaluator.completed_events] == [
        "turn.completed",
        "turn.completed",
    ]


def test_v4_supervisor_does_not_invoke_adversarial_evaluator_for_waiting_turn(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    evaluator = FakeAdversarialEvaluator()
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter([]),
        adversarial_evaluator=evaluator,
    )

    supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert evaluator.completed_events == []


def test_v4_supervisor_keeps_marker_only_source_turn_waiting(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter(
            lambda turn: [
                RuntimeEvent(
                    type="marker.detected",
                    turn_id=turn.turn_id,
                    worker_id=turn.worker_id,
                    payload={"marker": "marker-1"},
                ),
            ]
        ),
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "waiting"
    assert result["reason"] == "missing_outbox"


def test_v4_supervisor_delivers_turn_context_to_worker(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    adapter = FakeAdapter([])
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
        turn_context_builder=FakeTurnContextBuilder(),
    )

    supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    delivered_turn = adapter.delivered_turns[0]
    assert delivered_turn.unread_inbox_digest == "unread: review this"
    assert delivered_turn.unread_message_ids == ["msg-1"]
    assert delivered_turn.open_protocol_requests == [{"request_id": "req-1", "subject": "Review"}]
    assert delivered_turn.open_protocol_requests_digest == "open: Review"


def test_v4_supervisor_processes_valid_outbox_message_ack(tmp_path: Path):
    recorder = CrewRecorder(tmp_path / ".orchestrator")
    recorder.start_crew(CrewRecord(crew_id="crew-1", repo=str(tmp_path), root_goal="goal"))
    bus = AgentMessageBus(
        recorder,
        message_id_factory=iter(["msg-1"]).__next__,
        thread_id_factory=iter(["thread-1"]).__next__,
    )
    bus.send(
        crew_id="crew-1",
        sender="codex",
        recipient="worker-1",
        message_type=AgentMessageType.QUESTION,
        body="review this",
    )
    store = SQLiteEventStore(tmp_path / "events.sqlite3")

    def events_for_turn(turn: TurnEnvelope):
        return [
            RuntimeEvent(
                type="worker.outbox.detected",
                turn_id=turn.turn_id,
                worker_id=turn.worker_id,
                payload={
                    "valid": True,
                    "status": "completed",
                    "acknowledged_message_ids": ["msg-1"],
                },
            )
        ]

    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter(events_for_turn),
        turn_context_builder=TurnContextBuilder(bus),
        message_ack_processor=MessageAckProcessor(event_store=store, message_bus=bus),
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "turn_completed"
    assert bus.cursor_summary("crew-1") == {"worker-1": 1}
    assert bus.read_inbox(crew_id="crew-1", recipient="worker-1") == []
    assert "message.read" in [event.type for event in store.list_stream("crew-1")]


def test_v4_supervisor_assigns_canonical_required_outbox_path(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    adapter = FakeAdapter([])
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(paths.artifact_root),
        adapter=adapter,
        repo_root=tmp_path,
    )

    supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    expected_path = paths.outbox_path("worker-1", "round-1-worker-1-source")
    delivered_turn = adapter.delivered_turns[0]
    requested_event = next(
        event for event in store.list_stream("crew-1") if event.type == "turn.requested"
    )
    assert delivered_turn.required_outbox_path == str(expected_path)
    assert expected_path.parent.is_dir()
    assert requested_event.payload["required_outbox_path"] == str(expected_path)


def test_v4_supervisor_completes_tmux_turn_from_required_outbox_without_marker(
    tmp_path: Path,
):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    outbox_path = paths.outbox_path("worker-1", "round-1-worker-1-source")
    outbox_path.parent.mkdir(parents=True)
    outbox_path.write_text(
        json.dumps(
            {
                "crew_id": "crew-1",
                "worker_id": "worker-1",
                "turn_id": "round-1-worker-1-source",
                "status": "completed",
                "summary": "implemented",
            }
        ),
        encoding="utf-8",
    )
    native = FakeNativeSession()
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(paths.artifact_root),
        adapter=ClaudeCodeTmuxAdapter(native_session=native),
        repo_root=tmp_path,
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "turn_completed"
    assert [event.type for event in store.list_stream("crew-1")] == [
        "crew.started",
        "turn.requested",
        "turn.delivery_started",
        "turn.delivered",
        "worker.outbox.detected",
        "turn.completed",
    ]


def test_v4_supervisor_ignores_outbox_written_outside_required_path_without_marker(
    tmp_path: Path,
):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    wrong_outbox_path = paths.outbox_path("worker-1", "other-turn")
    wrong_outbox_path.parent.mkdir(parents=True)
    wrong_outbox_path.write_text(
        json.dumps(
            {
                "crew_id": "crew-1",
                "worker_id": "worker-1",
                "turn_id": "round-1-worker-1-source",
                "status": "completed",
                "summary": "wrong file",
            }
        ),
        encoding="utf-8",
    )
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(paths.artifact_root),
        adapter=ClaudeCodeTmuxAdapter(native_session=FakeNativeSession()),
        repo_root=tmp_path,
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "waiting"
    assert result["reason"] == "completion evidence not found"
    assert "worker.outbox.detected" not in [
        event.type for event in store.list_stream("crew-1")
    ]


def test_v4_supervisor_returns_waiting_for_inconclusive_turn(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter([]),
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "waiting"
    assert result["reason"] == "completion evidence not found"


def test_v4_supervisor_returns_delivery_failed_without_watching(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    adapter = FakeAdapter(
        delivery_result=DeliveryResult(
            delivered=False,
            marker="marker-1",
            reason="pane missing",
        )
    )
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "delivery_failed"
    assert result["reason"] == "pane missing"
    assert adapter.watched == []
    assert [event.type for event in store.list_stream("crew-1")] == [
        "crew.started",
        "turn.requested",
        "turn.delivery_started",
        "turn.delivery_failed",
    ]


def test_v4_supervisor_returns_waiting_when_delivery_already_in_progress(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    adapter = FakeAdapter(
        delivery_result=DeliveryResult(
            delivered=False,
            marker="marker-1",
            reason="delivery already in progress",
        )
    )
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "waiting"
    assert result["reason"] == "delivery already in progress"
    assert adapter.watched == []


def test_v4_supervisor_can_observe_late_outbox_after_inconclusive_turn(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    watches = [
        [{"text": "still working"}],
        ["outbox"],
    ]

    def events_for_turn(turn: TurnEnvelope):
        payloads = watches.pop(0)
        if payloads == ["outbox"]:
            return [completed_outbox_event(turn)]
        return [
            RuntimeEvent(
                type="output.chunk",
                turn_id=turn.turn_id,
                worker_id=turn.worker_id,
                payload=payload,
            )
            for payload in payloads
        ]

    adapter = FakeAdapter(events_for_turn)
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
    )

    first_result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )
    second_result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    events = store.list_stream("crew-1")
    assert first_result["status"] == "waiting"
    assert second_result["status"] == "turn_completed"
    assert len(adapter.watched) == 2
    assert len(adapter.delivered) == 1
    assert any(event.type == "turn.completed" for event in events)


def test_v4_supervisor_dedupes_identical_runtime_observation_on_repeat(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")

    def events_for_turn(turn: TurnEnvelope):
        return [
            RuntimeEvent(
                type="output.chunk",
                turn_id=turn.turn_id,
                worker_id=turn.worker_id,
                payload={"text": "still working"},
            )
        ]

    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter(events_for_turn),
    )

    for _ in range(2):
        supervisor.run_source_turn(
            crew_id="crew-1",
            goal="Fix tests",
            worker_id="worker-1",
            round_id="round-1",
            message="Implement",
            expected_marker="marker-1",
        )

    assert [event.type for event in store.list_stream("crew-1")].count("output.chunk") == 1


def test_v4_supervisor_preserves_completed_turn_on_repeat(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    watches = [
        ["outbox"],
        [],
    ]

    def events_for_turn(turn: TurnEnvelope):
        payloads = watches.pop(0)
        if payloads == ["outbox"]:
            return [completed_outbox_event(turn)]
        return []

    adapter = FakeAdapter(events_for_turn)
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
    )

    first_result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )
    second_result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert first_result["status"] == "turn_completed"
    assert second_result["status"] == "turn_completed"
    assert len(adapter.watched) == 1
    assert len(adapter.delivered) == 1
    assert [event.type for event in store.list_stream("crew-1")].count("turn.inconclusive") == 0


def test_v4_supervisor_resume_does_not_redeliver_completed_turn(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    adapter = FakeAdapter(
        lambda turn: [
            completed_outbox_event(turn)
        ]
    )
    first_supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
    )

    first_result = first_supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )
    resumed_supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
    )
    resumed_result = resumed_supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    events = store.list_stream("crew-1")
    assert first_result["status"] == "turn_completed"
    assert resumed_result["status"] == "turn_completed"
    assert adapter.delivered == ["round-1-worker-1-source"]
    assert adapter.watched == ["round-1-worker-1-source"]
    assert [event.type for event in events].count("turn.completed") == 1


def test_v4_supervisor_ignores_terminal_events_from_other_crews(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(
        stream_id="crew-2",
        type="turn.completed",
        crew_id="crew-2",
        worker_id="worker-1",
        turn_id="round-1-worker-1-source",
        idempotency_key="crew-2/round-1-worker-1-source/turn.completed",
        payload={"reason": "other crew completed"},
    )

    adapter = FakeAdapter(
        lambda turn: [
            completed_outbox_event(turn)
        ]
    )
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "turn_completed"
    assert adapter.delivered == ["round-1-worker-1-source"]
    assert adapter.watched == ["round-1-worker-1-source"]
    assert [event.type for event in store.list_stream("crew-1")] == [
        "crew.started",
        "turn.requested",
        "turn.delivery_started",
        "turn.delivered",
        "worker.outbox.detected",
        "turn.completed",
    ]


def test_v4_supervisor_ignores_mismatched_runtime_events(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter(
            [
                RuntimeEvent(
                    type="output.chunk",
                    turn_id="stale-turn",
                    worker_id="worker-1",
                    payload={"text": "done marker-1"},
                ),
            ]
        ),
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "waiting"
    assert [event.type for event in store.list_by_turn("round-1-worker-1-source")] == [
        "turn.requested",
        "turn.delivery_started",
        "turn.delivered",
        "turn.inconclusive",
    ]
