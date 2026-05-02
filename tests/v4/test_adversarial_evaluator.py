from codex_claude_orchestrator.v4.adversarial import AdversarialEvaluator
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore


def test_adversarial_evaluator_challenges_completed_turn_without_verification(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    completed = store.append(
        stream_id="crew-1",
        type="turn.completed",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
        payload={"reason": "valid outbox result detected"},
        artifact_refs=["workers/worker-1/outbox/turn-1.json"],
    )
    store.append(
        stream_id="crew-1",
        type="worker.outbox.detected",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
        payload={"valid": True, "status": "completed", "verification": []},
        artifact_refs=["workers/worker-1/outbox/turn-1.json"],
    )

    evaluator = AdversarialEvaluator(event_store=store)
    event = evaluator.evaluate_completed_turn(completed)

    assert event.type == "challenge.issued"
    assert event.payload["severity"] == "block"
    assert event.payload["category"] == "missing_verification"
    assert event.payload["source_event_ids"] == [completed.event_id]
    assert event.payload["repair_allowed"] is True


def test_adversarial_evaluator_records_pass_review_when_verification_passed(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    completed = store.append(
        stream_id="crew-1",
        type="turn.completed",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
    )
    store.append(
        stream_id="crew-1",
        type="worker.outbox.detected",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
        payload={
            "valid": True,
            "status": "completed",
            "verification": [{"command": "pytest tests/v4 -q", "status": "passed"}],
        },
    )
    store.append(
        stream_id="crew-1",
        type="verification.passed",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
        payload={"command": "pytest tests/v4 -q"},
    )

    evaluator = AdversarialEvaluator(event_store=store)
    event = evaluator.evaluate_completed_turn(completed)

    assert event.type == "review.completed"
    assert event.payload["verdict"] == "pass"
    assert event.payload["source_event_ids"] == [completed.event_id]


def test_adversarial_evaluator_ignores_invalid_outbox_verification(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    completed = store.append(
        stream_id="crew-1",
        type="turn.completed",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
    )
    store.append(
        stream_id="crew-1",
        type="worker.outbox.detected",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
        payload={
            "valid": False,
            "status": "completed",
            "verification": [{"command": "pytest tests/v4 -q", "status": "passed"}],
            "validation_errors": ["worker_id does not match watched worker"],
        },
    )

    evaluator = AdversarialEvaluator(event_store=store)
    event = evaluator.evaluate_completed_turn(completed)

    assert event.type == "challenge.issued"
    assert event.payload["category"] == "missing_verification"


def test_adversarial_evaluator_is_idempotent_for_same_completed_event(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    completed = store.append(
        stream_id="crew-1",
        type="turn.completed",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
        artifact_refs=["workers/worker-1/outbox/turn-1.json"],
    )

    evaluator = AdversarialEvaluator(event_store=store)
    first = evaluator.evaluate_completed_turn(completed)
    second = evaluator.evaluate_completed_turn(completed)

    assert first.event_id == second.event_id
    assert [event.type for event in store.list_stream("crew-1")] == [
        "turn.completed",
        "challenge.issued",
    ]
