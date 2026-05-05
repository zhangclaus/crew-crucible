from pathlib import Path

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.learning_feedback import GovernedLearningFeedback
from codex_claude_orchestrator.v4.paths import V4Paths


def test_learning_feedback_waits_until_failure_threshold(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    feedback = GovernedLearningFeedback(
        event_store=store,
        paths=V4Paths(repo_root=tmp_path, crew_id="crew-1"),
    )

    challenge = _append_challenge(
        store,
        worker_id="worker-1",
        category="review_block",
        finding="missing regression test",
    )

    assert feedback.record_challenge(challenge) == []
    assert [event.type for event in store.list_stream("crew-1")] == ["challenge.issued"]


def test_learning_feedback_creates_idempotent_learning_records_for_repeated_failure(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    feedback = GovernedLearningFeedback(
        event_store=store,
        paths=V4Paths(repo_root=tmp_path, crew_id="crew-1"),
    )

    first = _append_challenge(
        store,
        worker_id="worker-1",
        category="verification_failed",
        finding="unit tests failed",
    )
    assert feedback.record_challenge(first) == []

    second = _append_challenge(
        store,
        worker_id="worker-1",
        category="verification_failed",
        finding="unit tests still failed",
    )
    created = feedback.record_challenge(second)

    third = _append_challenge(
        store,
        worker_id="worker-1",
        category="verification_failed",
        finding="unit tests failed again",
    )
    replayed = feedback.record_challenge(third)

    assert [event.type for event in created] == [
        "learning.note_created",
        "guardrail.candidate_created",
        "worker.quality_updated",
    ]
    assert [event.event_id for event in replayed] == [event.event_id for event in created]
    assert [event.type for event in store.list_stream("crew-1")].count("learning.note_created") == 1
    assert [event.type for event in store.list_stream("crew-1")].count("guardrail.candidate_created") == 1
    assert [event.type for event in store.list_stream("crew-1")].count("worker.quality_updated") == 1
    assert created[2].payload["score_delta"] == -3
    assert created[2].payload["source_event_ids"] == [first.event_id, second.event_id]


def test_learning_feedback_ignores_categories_without_governed_policy(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    feedback = GovernedLearningFeedback(
        event_store=store,
        paths=V4Paths(repo_root=tmp_path, crew_id="crew-1"),
    )

    first = _append_challenge(
        store,
        worker_id="worker-1",
        category="write_scope",
        finding="changed file outside scope",
    )
    second = _append_challenge(
        store,
        worker_id="worker-1",
        category="write_scope",
        finding="changed another file outside scope",
    )

    assert feedback.record_challenge(first) == []
    assert feedback.record_challenge(second) == []
    assert [event.type for event in store.list_stream("crew-1")] == [
        "challenge.issued",
        "challenge.issued",
    ]


def _append_challenge(
    store: SQLiteEventStore,
    *,
    worker_id: str,
    category: str,
    finding: str,
):
    return store.append(
        stream_id="crew-1",
        type="challenge.issued",
        crew_id="crew-1",
        worker_id=worker_id,
        round_id="round-1",
        contract_id="source_write",
        payload={
            "challenge_id": f"challenge-{category}-{finding.replace(' ', '-')}",
            "severity": "block",
            "category": category,
            "finding": finding,
            "repair_allowed": True,
        },
    )
