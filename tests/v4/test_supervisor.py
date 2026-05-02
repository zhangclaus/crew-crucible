from pathlib import Path

from codex_claude_orchestrator.v4.artifacts import ArtifactStore
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.runtime import DeliveryResult, RuntimeEvent, TurnEnvelope
from codex_claude_orchestrator.v4.supervisor import V4Supervisor


class FakeAdapter:
    def __init__(self, events=None, delivery_result=None):
        self.events = events or []
        self.delivery_result = delivery_result
        self.delivered = []
        self.watched = []

    def deliver_turn(self, turn: TurnEnvelope):
        self.delivered.append(turn.turn_id)
        if self.delivery_result is not None:
            return self.delivery_result
        return DeliveryResult(delivered=True, marker=turn.expected_marker, reason="sent")

    def watch_turn(self, turn: TurnEnvelope):
        self.watched.append(turn.turn_id)
        events = self.events(turn) if callable(self.events) else self.events
        return iter(events)


def completed_outbox_event(turn: TurnEnvelope) -> RuntimeEvent:
    return RuntimeEvent(
        type="worker.outbox.detected",
        turn_id=turn.turn_id,
        worker_id=turn.worker_id,
        payload={"valid": True, "status": "completed"},
        artifact_refs=[f"workers/{turn.worker_id}/outbox/{turn.turn_id}.json"],
    )


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


def test_v4_supervisor_preserves_inconclusive_turn_on_repeat(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    watches = [
        [{"text": "still working"}],
        [{"text": "done marker-1"}],
    ]

    def events_for_turn(turn: TurnEnvelope):
        payloads = watches.pop(0)
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
    assert second_result["status"] == "waiting"
    assert second_result["reason"] == "completion evidence not found"
    assert len(adapter.watched) == 1
    assert len(adapter.delivered) == 1
    assert not any(event.type == "turn.completed" for event in events)


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
