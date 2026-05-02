from __future__ import annotations

from dataclasses import replace

from codex_claude_orchestrator.v4.completion import CompletionDetector
from codex_claude_orchestrator.v4.runtime import RuntimeEvent, TurnEnvelope


def make_turn() -> TurnEnvelope:
    return TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="implement",
        message="Finish the task",
        expected_marker="TURN_DONE",
        requires_structured_result=False,
    )


def test_expected_marker_completes_turn_with_artifact_evidence() -> None:
    decision = CompletionDetector.evaluate(
        make_turn(),
        [
            RuntimeEvent(
                type="output.chunk",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"text": "work finished TURN_DONE"},
                artifact_refs=["artifact-1"],
            ),
            RuntimeEvent(
                type="output.chunk",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"text": ""},
                artifact_refs=["artifact-2"],
            ),
        ],
    )

    assert decision.event_type == "turn.completed"
    assert decision.reason == "expected marker detected"
    assert decision.evidence_refs == ["artifact-1", "artifact-2"]


def test_marker_detected_event_completes_turn_with_artifact_evidence() -> None:
    decision = CompletionDetector.evaluate(
        make_turn(),
        [
            RuntimeEvent(
                type="marker.detected",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"marker": "TURN_DONE", "source": "tmux"},
                artifact_refs=["artifact-1"],
            )
        ],
    )

    assert decision.event_type == "turn.completed"
    assert decision.reason == "expected marker detected"
    assert decision.evidence_refs == ["artifact-1"]


def test_contract_marker_without_expected_marker_is_inconclusive() -> None:
    decision = CompletionDetector.evaluate(
        make_turn(),
        [
            RuntimeEvent(
                type="output.chunk",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"text": "done CONTRACT_DONE"},
                artifact_refs=["artifact-1"],
            )
        ],
        contract_marker="CONTRACT_DONE",
    )

    assert decision.event_type == "turn.inconclusive"
    assert decision.reason == "contract marker found but expected turn marker was missing"
    assert decision.evidence_refs == ["artifact-1"]


def test_timeout_without_completion_evidence_times_out() -> None:
    decision = CompletionDetector.evaluate(
        make_turn(),
        [
            RuntimeEvent(
                type="output.chunk",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"text": "still working"},
                artifact_refs=["artifact-1"],
            )
        ],
        timed_out=True,
    )

    assert decision.event_type == "turn.timeout"
    assert decision.reason == "deadline reached before completion evidence"
    assert decision.evidence_refs == ["artifact-1"]


def test_missing_and_non_string_output_text_falls_back_to_process_exit() -> None:
    decision = CompletionDetector.evaluate(
        make_turn(),
        [
            RuntimeEvent(
                type="output.chunk",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={},
                artifact_refs=["artifact-1"],
            ),
            RuntimeEvent(
                type="output.chunk",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"text": None},
                artifact_refs=["artifact-2"],
            ),
            RuntimeEvent(
                type="output.chunk",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"text": 42},
                artifact_refs=["artifact-3"],
            ),
            RuntimeEvent(
                type="process.exited",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={},
                artifact_refs=["artifact-4"],
            ),
        ],
    )

    assert decision.event_type == "turn.failed"
    assert decision.reason == "process exited before completion"
    assert decision.evidence_refs == [
        "artifact-1",
        "artifact-2",
        "artifact-3",
        "artifact-4",
    ]


def test_empty_expected_marker_does_not_complete() -> None:
    decision = CompletionDetector.evaluate(
        replace(make_turn(), expected_marker=""),
        [
            RuntimeEvent(
                type="output.chunk",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"text": "work finished"},
            )
        ],
    )

    assert decision.event_type == "turn.inconclusive"
    assert decision.reason == "completion evidence not found"


def test_evidence_refs_are_deduped_in_stable_order() -> None:
    decision = CompletionDetector.evaluate(
        make_turn(),
        [
            RuntimeEvent(
                type="output.chunk",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"text": "still working"},
                artifact_refs=["artifact-1", "artifact-2"],
            ),
            RuntimeEvent(
                type="process.exited",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"reason": "worker stopped"},
                artifact_refs=["artifact-2", "artifact-3", "artifact-1"],
            ),
        ],
    )

    assert decision.event_type == "turn.failed"
    assert decision.reason == "worker stopped"
    assert decision.evidence_refs == ["artifact-1", "artifact-2", "artifact-3"]


def test_missing_completion_evidence_is_inconclusive() -> None:
    decision = CompletionDetector.evaluate(
        make_turn(),
        [
            RuntimeEvent(
                type="output.chunk",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"text": "still working"},
            )
        ],
    )

    assert decision.event_type == "turn.inconclusive"
    assert decision.reason == "completion evidence not found"


def test_valid_outbox_evidence_completes_structured_turn() -> None:
    decision = CompletionDetector.evaluate(
        replace(make_turn(), requires_structured_result=True),
        [
            RuntimeEvent(
                type="worker.outbox.detected",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"valid": True},
                artifact_refs=["workers/worker-1/outbox/turn-1.json"],
            )
        ],
    )

    assert decision.event_type == "turn.completed"
    assert decision.reason == "valid outbox result detected"
    assert decision.evidence_refs == ["workers/worker-1/outbox/turn-1.json"]


def test_source_write_marker_without_outbox_is_inconclusive() -> None:
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="work",
        expected_marker="DONE",
        contract_id="contract-1",
        requires_structured_result=True,
    )
    decision = CompletionDetector.evaluate(
        turn,
        [
            RuntimeEvent(
                type="marker.detected",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"marker": "DONE"},
            )
        ],
    )

    assert decision.event_type == "turn.inconclusive"
    assert decision.reason == "missing_outbox"


def test_marker_allowed_turn_can_complete_without_outbox() -> None:
    turn = replace(
        make_turn(),
        requires_structured_result=True,
        completion_mode="marker_allowed",
    )

    decision = CompletionDetector.evaluate(
        turn,
        [
            RuntimeEvent(
                type="marker.detected",
                turn_id="turn-1",
                worker_id="worker-1",
                payload={"marker": "TURN_DONE"},
            )
        ],
    )

    assert decision.event_type == "turn.completed"
    assert decision.reason == "expected marker detected"
