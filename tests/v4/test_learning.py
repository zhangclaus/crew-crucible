import json

import pytest

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.learning import (
    GuardrailMemory,
    LearningRecorder,
    SkillCandidateGate,
    WorkerQualityTracker,
)
from codex_claude_orchestrator.v4.paths import V4Paths


def test_learning_recorder_writes_note_artifact_and_event(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    recorder = LearningRecorder(event_store=store, paths=paths)

    event = recorder.create_note(
        note_id="note-1",
        source_challenge_ids=["challenge-1"],
        source_event_ids=["evt-challenge-1"],
        failure_class="missing_verification",
        lesson="Repairs need passed verification evidence.",
        trigger_conditions=["repair turn", "worker claims completion"],
        scope="v4 worker turn review",
    )

    note_path = paths.learning_note_path("note-1")
    assert event.type == "learning.note_created"
    assert event.artifact_refs == ["learning/notes/note-1.json"]
    assert (
        json.loads(note_path.read_text(encoding="utf-8"))["lesson"]
        == "Repairs need passed verification evidence."
    )


def test_skill_candidate_approval_does_not_activate(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    gate = SkillCandidateGate(event_store=store, paths=paths)

    created = gate.create_candidate(
        candidate_id="skill-1",
        source_note_ids=["note-1"],
        source_event_ids=["evt-note-1"],
        summary="Require verification evidence before accepting repair turns.",
        trigger_conditions=["repair turn"],
        body="Check repair outbox verification before readiness.",
    )
    approved = gate.approve_candidate(
        candidate_id="skill-1",
        decision_reason="Narrow and backed by evidence.",
        approver="human",
        decided_at="2026-05-02T00:00:00Z",
    )

    assert created.type == "skill.candidate_created"
    assert created.payload["activation_state"] == "pending"
    assert approved.type == "skill.approved"
    assert "active_artifact_ref" not in approved.payload
    assert [event.type for event in store.list_stream("crew-1")] == [
        "skill.candidate_created",
        "skill.approved",
    ]


def test_skill_candidate_activation_requires_existing_candidate(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    gate = SkillCandidateGate(event_store=store, paths=paths)

    gate.create_candidate(
        candidate_id="skill-1",
        source_note_ids=["note-1"],
        source_event_ids=["evt-note-1"],
        summary="Require verification evidence before accepting repair turns.",
        trigger_conditions=["repair turn"],
        body="Check repair outbox verification before readiness.",
    )
    event = gate.activate_candidate(
        candidate_id="skill-1",
        activation_id="activation-1",
        activated_by="human",
        activated_at="2026-05-02T00:01:00Z",
    )

    assert event.type == "skill.activated"
    assert event.payload["active_artifact_ref"] == "learning/skill_candidates/skill-1.json"


def test_skill_activation_missing_candidate_raises_file_not_found(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    gate = SkillCandidateGate(event_store=store, paths=paths)

    with pytest.raises(FileNotFoundError):
        gate.activate_candidate(
            candidate_id="skill-1",
            activation_id="activation-1",
            activated_by="human",
            activated_at="2026-05-02T00:01:00Z",
        )


def test_guardrail_candidate_lifecycle_matches_skill_candidate_lifecycle(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    memory = GuardrailMemory(event_store=store, paths=paths)

    created = memory.create_candidate(
        candidate_id="guardrail-1",
        source_note_ids=["note-1"],
        source_event_ids=["evt-note-1"],
        rule_summary="Block readiness when repair has no passed verification event.",
        enforcement_point="readiness",
        trigger_conditions=["repair.completed"],
    )
    rejected = memory.reject_candidate(
        candidate_id="guardrail-1",
        decision_reason="Too broad for automatic enforcement.",
        approver="human",
        decided_at="2026-05-02T00:03:00Z",
    )

    assert created.type == "guardrail.candidate_created"
    assert rejected.type == "guardrail.rejected"


def test_guardrail_activation_missing_candidate_raises_file_not_found(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    memory = GuardrailMemory(event_store=store, paths=paths)

    with pytest.raises(FileNotFoundError):
        memory.activate_candidate(
            candidate_id="guardrail-1",
            activation_id="activation-1",
            activated_by="human",
            activated_at="2026-05-02T00:01:00Z",
        )


def test_worker_quality_tracker_records_score_delta(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    tracker = WorkerQualityTracker(event_store=store, paths=paths)

    event = tracker.update_quality(
        worker_id="worker-1",
        score_delta=-2,
        reason_codes=["missing_verification"],
        source_event_ids=["evt-challenge-1"],
        expires_at="2026-06-02T00:00:00Z",
    )

    assert event.type == "worker.quality_updated"
    assert event.payload["score_delta"] == -2
    assert (
        json.loads(paths.worker_quality_path.read_text(encoding="utf-8"))["worker-1"][
            "score"
        ]
        == -2
    )
