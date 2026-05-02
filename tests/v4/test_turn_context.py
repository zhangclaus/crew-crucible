from __future__ import annotations

from pathlib import Path
from typing import Any

from codex_claude_orchestrator.crew.models import AgentMessageType, CrewRecord
from codex_claude_orchestrator.messaging.message_bus import AgentMessageBus
from codex_claude_orchestrator.messaging.protocol_requests import ProtocolRequestStore
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.v4.turn_context import TurnContextBuilder


def test_turn_context_builds_unread_digest_without_marking_read(tmp_path: Path) -> None:
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

    context = TurnContextBuilder(bus).build(crew_id="crew-1", worker_id="worker-1")

    assert context.unread_count == 1
    assert context.unread_message_ids == ["msg-1"]
    assert "msg-1" in context.unread_inbox_digest
    assert "review this" in context.unread_inbox_digest
    assert bus.cursor_summary("crew-1") == {}


def test_turn_context_reads_inbox_without_advancing_cursor() -> None:
    class RecordingBus:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def read_inbox(self, *, crew_id: str, recipient: str, mark_read: bool = False) -> list[dict]:
            self.calls.append({"crew_id": crew_id, "recipient": recipient, "mark_read": mark_read})
            return [
                {
                    "message_id": "msg-1",
                    "from": "codex",
                    "to": recipient,
                    "type": "status",
                    "body": "first line\nsecond line",
                }
            ]

    bus = RecordingBus()

    context = TurnContextBuilder(bus).build(crew_id="crew-1", worker_id="worker-1")

    assert bus.calls == [{"crew_id": "crew-1", "recipient": "worker-1", "mark_read": False}]
    assert context.unread_count == 1
    assert context.unread_message_ids == ["msg-1"]
    assert "first line second line" in context.unread_inbox_digest


def test_turn_context_includes_open_protocol_requests(tmp_path: Path) -> None:
    recorder = CrewRecorder(tmp_path / ".orchestrator")
    recorder.start_crew(CrewRecord(crew_id="crew-1", repo=str(tmp_path), root_goal="goal"))
    bus = AgentMessageBus(recorder)
    protocol_requests = ProtocolRequestStore(
        recorder,
        request_id_factory=iter(["req-1"]).__next__,
    )
    protocol_requests.create(
        crew_id="crew-1",
        request_type="review",
        sender="codex",
        recipient="worker-1",
        subject="Review patch",
        body="Check the diff",
    )

    context = TurnContextBuilder(bus, protocol_request_store=protocol_requests).build(
        crew_id="crew-1",
        worker_id="worker-1",
    )

    assert context.open_protocol_requests == [
        {
            "request_id": "req-1",
            "type": "review",
            "from": "codex",
            "to": "worker-1",
            "status": "pending",
            "subject": "Review patch",
            "body": "Check the diff",
            "artifact_refs": [],
        }
    ]
    assert "Review patch" in context.open_protocol_requests_digest
