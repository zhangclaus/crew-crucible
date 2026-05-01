from pathlib import Path

import pytest

from codex_claude_orchestrator.crew.models import ProtocolRequestStatus
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.messaging.protocol_requests import ProtocolRequestStore


def test_protocol_request_store_records_pending_to_approved_flow(tmp_path: Path):
    store = ProtocolRequestStore(
        CrewRecorder(tmp_path / ".orchestrator"),
        request_id_factory=lambda: "req-1",
    )

    pending = store.create(
        crew_id="crew-1",
        request_type="plan_request",
        sender="worker-source",
        recipient="codex",
        subject="Edit verification pipeline",
        body="I plan to update the runner and tests.",
    )
    approved = store.transition(
        crew_id="crew-1",
        request_id="req-1",
        status=ProtocolRequestStatus.APPROVED,
        reason="scope is narrow",
    )

    history = store.list_requests("crew-1")
    assert pending.status == ProtocolRequestStatus.PENDING
    assert approved.status == ProtocolRequestStatus.APPROVED
    assert [item["status"] for item in history] == ["pending", "approved"]
    assert history[-1]["reason"] == "scope is narrow"


def test_protocol_request_store_rejects_transition_from_terminal_status(tmp_path: Path):
    store = ProtocolRequestStore(
        CrewRecorder(tmp_path / ".orchestrator"),
        request_id_factory=lambda: "req-1",
    )
    store.create(
        crew_id="crew-1",
        request_type="shutdown_request",
        sender="codex",
        recipient="worker-source",
        subject="Stop after handoff",
    )
    store.transition(crew_id="crew-1", request_id="req-1", status=ProtocolRequestStatus.REJECTED)

    with pytest.raises(ValueError, match="terminal protocol request"):
        store.transition(crew_id="crew-1", request_id="req-1", status=ProtocolRequestStatus.APPROVED)
