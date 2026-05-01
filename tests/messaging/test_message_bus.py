from pathlib import Path

import pytest

from codex_claude_orchestrator.messaging.message_bus import AgentMessageBus, parse_codex_message_blocks
from codex_claude_orchestrator.crew.models import AgentMessageType
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder


def test_message_bus_appends_messages_to_global_log_inbox_and_cursor(tmp_path: Path):
    recorder = CrewRecorder(tmp_path / ".orchestrator")
    bus = AgentMessageBus(
        recorder,
        message_id_factory=lambda: "msg-1",
        thread_id_factory=lambda: "thread-1",
    )

    message = bus.send(
        crew_id="crew-1",
        sender="worker-a",
        recipient="worker-b",
        message_type=AgentMessageType.HANDOFF,
        body="API findings are in artifact workers/a/findings.md",
        artifact_refs=["workers/a/findings.md"],
        requires_response=True,
    )
    unread = bus.read_inbox(crew_id="crew-1", recipient="worker-b", mark_read=True)
    reread = bus.read_inbox(crew_id="crew-1", recipient="worker-b", mark_read=True)

    assert message.message_id == "msg-1"
    assert unread == [message.to_dict()]
    assert reread == []
    assert bus.cursor_summary("crew-1") == {"worker-b": 1}
    assert (tmp_path / ".orchestrator" / "crews" / "crew-1" / "messages.jsonl").exists()
    assert (tmp_path / ".orchestrator" / "crews" / "crew-1" / "inboxes" / "worker-b.jsonl").exists()


def test_parse_codex_message_blocks_extracts_structured_worker_messages():
    snapshot = """worker output
<<<CODEX_MESSAGE
to: codex
type: question
requires_response: true
body: Need a readonly API auditor before editing.
>>>
more output
<<<CODEX_MESSAGE
to: contract:patch-risk-auditor
type: handoff
body:
Changed files are attached.
Please review API compatibility.
artifact_refs: workers/source/diff.patch, workers/source/changes.json
>>>
"""

    messages = parse_codex_message_blocks(
        snapshot,
        crew_id="crew-1",
        sender="worker-source",
        message_id_factory=lambda: "msg-fixed",
        thread_id_factory=lambda: "thread-fixed",
    )

    assert [message.recipient for message in messages] == ["codex", "contract:patch-risk-auditor"]
    assert [message.type for message in messages] == [AgentMessageType.QUESTION, AgentMessageType.HANDOFF]
    assert messages[0].requires_response is True
    assert messages[1].body == "Changed files are attached.\nPlease review API compatibility."
    assert messages[1].artifact_refs == ["workers/source/diff.patch", "workers/source/changes.json"]


def test_parse_codex_message_blocks_rejects_unknown_message_type():
    with pytest.raises(ValueError, match="unsupported CODEX_MESSAGE type"):
        parse_codex_message_blocks(
            "<<<CODEX_MESSAGE\nto: codex\ntype: freestyle\nbody: nope\n>>>",
            crew_id="crew-1",
            sender="worker-a",
        )
