from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.learning_projection import LearningProjection


def test_learning_projection_keeps_unresolved_challenge_open(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(
        stream_id="crew-1",
        type="challenge.issued",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        payload={"challenge_id": "challenge-1", "severity": "block"},
    )

    projection = LearningProjection.from_events(store.list_stream("crew-1"))

    assert projection.open_challenge_ids == ["challenge-1"]
    assert projection.has_blocking_challenge is True


def test_learning_projection_closes_challenge_after_fixed_repair(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(
        stream_id="crew-1",
        type="challenge.issued",
        crew_id="crew-1",
        payload={"challenge_id": "challenge-1", "severity": "block"},
    )
    store.append(
        stream_id="crew-1",
        type="repair.completed",
        crew_id="crew-1",
        payload={"challenge_id": "challenge-1", "outcome": "fixed"},
    )

    projection = LearningProjection.from_events(store.list_stream("crew-1"))

    assert projection.open_challenge_ids == []
    assert projection.has_blocking_challenge is False


def test_learning_projection_requires_activation_before_candidate_is_active(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(
        stream_id="crew-1",
        type="skill.candidate_created",
        crew_id="crew-1",
        payload={"candidate_id": "skill-1", "activation_state": "pending"},
    )
    store.append(
        stream_id="crew-1",
        type="skill.approved",
        crew_id="crew-1",
        payload={"candidate_id": "skill-1", "decision": "approved"},
    )

    approved_only = LearningProjection.from_events(store.list_stream("crew-1"))
    assert approved_only.active_skill_refs == []

    store.append(
        stream_id="crew-1",
        type="skill.activated",
        crew_id="crew-1",
        payload={
            "candidate_id": "skill-1",
            "active_artifact_ref": "learning/skill_candidates/skill-1.json",
        },
    )
    activated = LearningProjection.from_events(store.list_stream("crew-1"))
    assert activated.active_skill_refs == ["learning/skill_candidates/skill-1.json"]


def test_learning_projection_tracks_guardrail_activation_and_worker_quality(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(
        stream_id="crew-1",
        type="guardrail.candidate_created",
        crew_id="crew-1",
        payload={"candidate_id": "guardrail-1"},
    )
    store.append(
        stream_id="crew-1",
        type="guardrail.activated",
        crew_id="crew-1",
        payload={
            "candidate_id": "guardrail-1",
            "active_artifact_ref": "learning/guardrail_candidates/guardrail-1.json",
        },
    )
    store.append(
        stream_id="crew-1",
        type="worker.quality_updated",
        crew_id="crew-1",
        worker_id="worker-1",
        payload={"worker_id": "worker-1", "score_delta": -2},
    )
    store.append(
        stream_id="crew-1",
        type="worker.quality_updated",
        crew_id="crew-1",
        worker_id="worker-1",
        payload={"worker_id": "worker-1", "score_delta": 1},
    )

    projection = LearningProjection.from_events(store.list_stream("crew-1"))

    assert projection.active_guardrail_refs == ["learning/guardrail_candidates/guardrail-1.json"]
    assert projection.worker_quality_scores == {"worker-1": -1}
