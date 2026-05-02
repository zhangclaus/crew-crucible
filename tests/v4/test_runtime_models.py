from __future__ import annotations

from pathlib import Path

import pytest

from codex_claude_orchestrator.v4.runtime import RuntimeEvent, TurnEnvelope, WorkerSpec


def test_turn_envelope_idempotency_key_includes_phase() -> None:
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="implement",
        message="Add runtime models",
        expected_marker="DONE",
    )

    assert turn.idempotency_key == "crew-1/worker-1/turn-1/implement"


def test_turn_envelope_defaults_to_structured_completion() -> None:
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="implement",
        message="Add runtime models",
        expected_marker="DONE",
        contract_id="contract-1",
    )

    assert turn.contract_id == "contract-1"
    assert turn.completion_mode == "structured_required"
    assert turn.requires_structured_result is True


def test_runtime_event_to_dict_normalizes_payload() -> None:
    event = RuntimeEvent(
        type="turn.completed",
        turn_id="turn-1",
        worker_id="worker-1",
        payload={"artifact": Path("artifacts/result.txt")},
        artifact_refs=["artifacts/result.txt"],
    )

    assert event.to_dict() == {
        "type": "turn.completed",
        "turn_id": "turn-1",
        "worker_id": "worker-1",
        "payload": {"artifact": "artifacts/result.txt"},
        "artifact_refs": ["artifacts/result.txt"],
    }


def test_worker_spec_rejects_missing_runtime_type() -> None:
    with pytest.raises(ValueError, match="runtime_type is required"):
        WorkerSpec(
            crew_id="crew-1",
            worker_id="worker-1",
            runtime_type="",
            contract_id="contract-1",
        )
