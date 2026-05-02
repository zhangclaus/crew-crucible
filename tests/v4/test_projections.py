from pathlib import Path

import pytest

from codex_claude_orchestrator.v4.events import AgentEvent
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.projections import CrewProjection


def test_projection_builds_turn_status_from_events(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(stream_id="crew-1", type="crew.started", crew_id="crew-1", payload={"goal": "Fix tests"})
    store.append(stream_id="crew-1", type="turn.requested", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")
    store.append(stream_id="crew-1", type="turn.delivered", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")
    store.append(stream_id="crew-1", type="turn.completed", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")

    projection = CrewProjection.from_events(store.list_stream("crew-1"))

    assert projection.crew_id == "crew-1"
    assert projection.goal == "Fix tests"
    assert projection.turns["turn-1"].status == "completed"


def test_projection_reports_waiting_turn():
    projection = CrewProjection.from_events([])

    assert projection.status == "empty"
    assert projection.turns == {}


@pytest.mark.parametrize(
    ("crew_event_type", "expected_status"),
    [
        ("crew.ready_for_accept", "ready"),
        ("human.required", "needs_human"),
        ("crew.accepted", "accepted"),
    ],
)
def test_projection_keeps_terminal_status_after_later_turn_event(
    crew_event_type: str,
    expected_status: str,
):
    events = [
        AgentEvent(
            event_id="evt-1",
            stream_id="crew-1",
            sequence=1,
            type="crew.started",
            crew_id="crew-1",
            payload={"goal": "Fix tests"},
        ),
        AgentEvent(
            event_id="evt-2",
            stream_id="crew-1",
            sequence=2,
            type=crew_event_type,
            crew_id="crew-1",
        ),
        AgentEvent(
            event_id="evt-3",
            stream_id="crew-1",
            sequence=3,
            type="turn.completed",
            crew_id="crew-1",
            worker_id="worker-1",
            turn_id="turn-1",
        ),
    ]

    projection = CrewProjection.from_events(events)

    assert projection.status == expected_status
    assert projection.turns["turn-1"].status == "completed"


def test_projection_rejects_mixed_crew_ids_but_ignores_empty_crew_id():
    events = [
        AgentEvent(
            event_id="evt-1",
            stream_id="global",
            sequence=1,
            type="turn.requested",
            worker_id="worker-1",
            turn_id="turn-1",
        ),
        AgentEvent(
            event_id="evt-2",
            stream_id="crew-1",
            sequence=2,
            type="crew.started",
            crew_id="crew-1",
            payload={"goal": "Fix tests"},
        ),
        AgentEvent(
            event_id="evt-3",
            stream_id="crew-2",
            sequence=3,
            type="turn.completed",
            crew_id="crew-2",
            worker_id="worker-2",
            turn_id="turn-2",
        ),
    ]

    with pytest.raises(ValueError, match="mixed crew ids"):
        CrewProjection.from_events(events)


def test_crew_projection_surfaces_learning_blockers(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(stream_id="crew-1", type="crew.started", crew_id="crew-1", payload={"goal": "Fix"})
    store.append(
        stream_id="crew-1",
        type="challenge.issued",
        crew_id="crew-1",
        payload={"challenge_id": "challenge-1", "severity": "block"},
    )

    projection = CrewProjection.from_events(store.list_stream("crew-1"))

    assert projection.status == "needs_human"
    assert projection.learning.has_blocking_challenge is True
