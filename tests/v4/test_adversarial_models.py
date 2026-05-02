from codex_claude_orchestrator.v4.adversarial_models import (
    ActivationPayload,
    ApprovalPayload,
    CandidatePayload,
    ChallengeIssuePayload,
    ChallengeSeverity,
    LearningNotePayload,
    RepairOutcome,
    RepairRequestPayload,
    WorkerPolicy,
    WorkerQualityPayload,
)


def test_challenge_issue_payload_normalizes_for_events() -> None:
    payload = ChallengeIssuePayload(
        challenge_id="challenge-1",
        source_turn_id="turn-1",
        source_event_ids=["evt-turn-completed"],
        severity=ChallengeSeverity.BLOCK,
        category="missing_verification",
        finding="Worker claimed success without verification evidence.",
        required_response="Write a repair outbox with verification evidence.",
        repair_allowed=True,
        artifact_refs=["workers/worker-1/outbox/turn-1.json"],
    )

    assert payload.to_payload() == {
        "challenge_id": "challenge-1",
        "source_turn_id": "turn-1",
        "source_event_ids": ["evt-turn-completed"],
        "severity": "block",
        "category": "missing_verification",
        "finding": "Worker claimed success without verification evidence.",
        "required_response": "Write a repair outbox with verification evidence.",
        "repair_allowed": True,
        "artifact_refs": ["workers/worker-1/outbox/turn-1.json"],
    }


def test_repair_request_payload_keeps_worker_policy_explicit() -> None:
    payload = RepairRequestPayload(
        challenge_id="challenge-1",
        repair_contract_id="contract-repair-1",
        repair_turn_id="turn-repair-1",
        worker_policy=WorkerPolicy.FRESH_WORKER,
        allowed_write_scope=["src/**/*.py", "tests/**/*.py"],
        acceptance_criteria=["Focused regression test passes."],
        required_outbox_path="workers/worker-2/outbox/turn-repair-1.json",
    )

    assert payload.to_payload()["worker_policy"] == "fresh_worker"
    assert payload.to_payload()["allowed_write_scope"] == ["src/**/*.py", "tests/**/*.py"]


def test_learning_candidate_approval_and_activation_payloads_are_distinct() -> None:
    candidate = CandidatePayload(
        candidate_id="skill-candidate-1",
        source_note_ids=["note-1"],
        source_event_ids=["evt-note-1"],
        kind="skill",
        summary="Require focused regression tests for bug repairs.",
        trigger_conditions=["bug repair", "regression risk"],
        artifact_ref="learning/skill_candidates/skill-candidate-1.json",
    )
    approved = ApprovalPayload(
        candidate_id="skill-candidate-1",
        decision="approved",
        decision_reason="Narrow and evidence-backed.",
        approver="human",
        decided_at="2026-05-02T00:00:00Z",
    )
    activated = ActivationPayload(
        candidate_id="skill-candidate-1",
        activation_id="activation-1",
        activated_by="human",
        activated_at="2026-05-02T00:01:00Z",
        active_artifact_ref="learning/skill_candidates/skill-candidate-1.json",
        rollback_plan="Append skill.rejected or remove active artifact ref through a follow-up activation event.",
    )

    assert candidate.to_payload()["activation_state"] == "pending"
    assert candidate.to_payload()["approval_required"] is True
    assert approved.to_payload()["decision"] == "approved"
    assert "active_artifact_ref" not in approved.to_payload()
    assert activated.to_payload()["active_artifact_ref"] == "learning/skill_candidates/skill-candidate-1.json"


def test_learning_note_payload_uses_governed_learning_spec_fields() -> None:
    payload = LearningNotePayload(
        note_id="note-1",
        source_challenge_ids=["challenge-1"],
        source_event_ids=["evt-challenge-1"],
        failure_class="missing_verification",
        lesson="Repairs need passed verification evidence.",
        trigger_conditions=["repair turn", "worker claims completion"],
        scope="v4 worker turn review",
    )

    assert payload.to_payload() == {
        "note_id": "note-1",
        "source_challenge_ids": ["challenge-1"],
        "source_event_ids": ["evt-challenge-1"],
        "failure_class": "missing_verification",
        "lesson": "Repairs need passed verification evidence.",
        "trigger_conditions": ["repair turn", "worker claims completion"],
        "scope": "v4 worker turn review",
    }


def test_worker_quality_payload_includes_expiry() -> None:
    payload = WorkerQualityPayload(
        worker_id="worker-1",
        score_delta=-2,
        reason_codes=["missing_verification", "repair_required"],
        source_event_ids=["evt-challenge-1"],
        expires_at="2026-06-02T00:00:00Z",
    )

    assert payload.to_payload()["score_delta"] == -2
    assert payload.to_payload()["expires_at"] == "2026-06-02T00:00:00Z"
