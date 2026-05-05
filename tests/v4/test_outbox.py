from __future__ import annotations

import pytest

from codex_claude_orchestrator.v4.outbox import WorkerOutboxResult


def test_outbox_result_parses_acknowledged_message_ids() -> None:
    result = WorkerOutboxResult.from_dict(
        {
            "crew_id": "crew-1",
            "worker_id": "worker-1",
            "turn_id": "turn-1",
            "status": "completed",
            "summary": "done",
            "changed_files": ["src/app.py"],
            "artifact_refs": ["artifacts/result.json"],
            "verification": [{"command": "pytest -q", "status": "passed"}],
            "acknowledged_message_ids": ["msg-1"],
            "messages": [{"to": "codex", "body": "done"}],
            "risks": ["none"],
            "next_suggested_action": "review",
        }
    )

    assert result.is_valid
    assert result.validation_errors == []
    assert result.acknowledged_message_ids == ["msg-1"]
    assert result.verification == [{"command": "pytest -q", "status": "passed"}]
    assert result.messages == [{"to": "codex", "body": "done"}]
    assert result.risks == ["none"]
    assert result.next_suggested_action == "review"


def test_outbox_result_parses_typed_review_verdict() -> None:
    result = WorkerOutboxResult.from_dict(
        {
            "crew_id": "crew-1",
            "worker_id": "worker-review",
            "turn_id": "turn-review",
            "status": "completed",
            "review": {
                "verdict": "block",
                "summary": "missing regression test",
                "findings": ["add a failing test first"],
                "evidence_refs": ["workers/worker-review/outbox/turn-review.json"],
            },
        }
    )

    assert result.is_valid
    assert result.review == {
        "verdict": "block",
        "summary": "missing regression test",
        "findings": ["add a failing test first"],
        "evidence_refs": ["workers/worker-review/outbox/turn-review.json"],
    }


def test_outbox_result_reports_validation_errors_without_crashing() -> None:
    result = WorkerOutboxResult.from_dict(
        {
            "crew_id": "",
            "worker_id": None,
            "turn_id": 12,
            "status": "done",
            "changed_files": "src/app.py",
            "artifact_refs": ["artifacts/result.json", 7],
            "verification": "pytest -q",
            "acknowledged_message_ids": [None],
            "messages": {"body": "not a list"},
            "risks": [object()],
        }
    )

    assert not result.is_valid
    assert "crew_id is required" in result.validation_errors
    assert "worker_id is required" in result.validation_errors
    assert "turn_id is required" in result.validation_errors
    assert "status must be one of: blocked, completed, failed, inconclusive" in result.validation_errors
    assert "changed_files must be a list of strings" in result.validation_errors
    assert "artifact_refs must be a list of strings" in result.validation_errors
    assert "verification must be a list" in result.validation_errors
    assert "acknowledged_message_ids must be a list of strings" in result.validation_errors
    assert "messages must be a list" in result.validation_errors
    assert "risks must be a list of strings" in result.validation_errors


def test_outbox_result_rejects_non_dict_payload() -> None:
    with pytest.raises(TypeError, match="payload must be a dict"):
        WorkerOutboxResult.from_dict(["not", "a", "dict"])
