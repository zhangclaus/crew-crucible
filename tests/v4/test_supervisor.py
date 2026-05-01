from pathlib import Path

from codex_claude_orchestrator.v4.artifacts import ArtifactStore
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.runtime import DeliveryResult, RuntimeEvent, TurnEnvelope
from codex_claude_orchestrator.v4.supervisor import V4Supervisor


class FakeAdapter:
    def __init__(self, events):
        self.events = events
        self.delivered = []

    def deliver_turn(self, turn: TurnEnvelope):
        self.delivered.append(turn.turn_id)
        return DeliveryResult(delivered=True, marker=turn.expected_marker, reason="sent")

    def watch_turn(self, turn: TurnEnvelope):
        return iter(self.events)


def test_v4_supervisor_runs_until_turn_completed(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter(
            [
                RuntimeEvent(
                    type="output.chunk",
                    turn_id="turn-1",
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

    assert result["status"] == "turn_completed"
    assert [event.type for event in store.list_stream("crew-1")] == [
        "crew.started",
        "turn.requested",
        "turn.delivery_started",
        "turn.delivered",
        "output.chunk",
        "turn.completed",
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
