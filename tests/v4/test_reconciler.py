from pathlib import Path
from typing import get_type_hints

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.reconciler import Reconciler


def test_reconciler_depends_on_event_store_protocol():
    annotation = get_type_hints(Reconciler.__init__)["event_store"]

    assert annotation is EventStore


def test_reconciler_marks_delivered_turn_without_completion_as_inconclusive(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(stream_id="crew-1", type="turn.requested", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")
    store.append(stream_id="crew-1", type="turn.delivered", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")

    event = Reconciler(event_store=store).reconcile_turn("crew-1", "turn-1")

    assert event.type == "turn.inconclusive"
    assert "delivered without completion" in event.payload["reason"]


def test_reconciler_does_not_duplicate_existing_completion(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(stream_id="crew-1", type="turn.delivered", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")
    store.append(stream_id="crew-1", type="turn.completed", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")

    event = Reconciler(event_store=store).reconcile_turn("crew-1", "turn-1")

    assert event is None


def test_reconciler_ignores_other_crew_terminal_event_for_same_turn_id(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(stream_id="crew-1", type="turn.delivered", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")
    store.append(stream_id="crew-2", type="turn.completed", crew_id="crew-2", worker_id="worker-2", turn_id="turn-1")

    event = Reconciler(event_store=store).reconcile_turn("crew-1", "turn-1")

    assert event is not None
    assert event.type == "turn.inconclusive"
    assert event.crew_id == "crew-1"
    assert event.worker_id == "worker-1"


def test_reconciler_ignores_other_crew_delivered_event_for_same_turn_id(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(
        stream_id="crew-2",
        type="turn.delivered",
        crew_id="crew-2",
        worker_id="worker-2",
        turn_id="turn-1",
        artifact_refs=["artifact-from-crew-2"],
    )

    event = Reconciler(event_store=store).reconcile_turn("crew-1", "turn-1")

    assert event is None
