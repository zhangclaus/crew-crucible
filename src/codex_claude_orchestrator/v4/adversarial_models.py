"""Typed payload models for V4 adversarial evaluation and governed learning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from codex_claude_orchestrator.v4.events import normalize


class ChallengeSeverity(StrEnum):
    WARN = "warn"
    BLOCK = "block"


class WorkerPolicy(StrEnum):
    SAME_WORKER = "same_worker"
    FRESH_WORKER = "fresh_worker"
    HUMAN_REQUIRED = "human_required"


class RepairOutcome(StrEnum):
    FIXED = "fixed"
    NOT_FIXED = "not_fixed"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class _PayloadModel:
    def to_payload(self) -> dict[str, Any]:
        return normalize(asdict(self))


@dataclass(frozen=True, slots=True)
class ChallengeIssuePayload(_PayloadModel):
    challenge_id: str
    source_turn_id: str
    source_event_ids: list[str]
    severity: ChallengeSeverity
    category: str
    finding: str
    required_response: str
    repair_allowed: bool
    artifact_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ChallengeAnswerPayload(_PayloadModel):
    challenge_id: str
    answer_event_ids: list[str]
    answer: str
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RepairRequestPayload(_PayloadModel):
    challenge_id: str
    repair_contract_id: str
    repair_turn_id: str
    worker_policy: WorkerPolicy
    allowed_write_scope: list[str]
    acceptance_criteria: list[str]
    required_outbox_path: str


@dataclass(frozen=True, slots=True)
class RepairCompletedPayload(_PayloadModel):
    challenge_id: str
    repair_contract_id: str
    repair_turn_id: str
    outcome: RepairOutcome
    summary: str
    verification_event_ids: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LearningNotePayload(_PayloadModel):
    note_id: str
    source_challenge_ids: list[str]
    source_event_ids: list[str]
    failure_class: str
    lesson: str
    trigger_conditions: list[str]
    scope: str


@dataclass(frozen=True, slots=True)
class CandidatePayload(_PayloadModel):
    candidate_id: str
    source_note_ids: list[str]
    source_event_ids: list[str]
    kind: str
    summary: str
    trigger_conditions: list[str]
    artifact_ref: str
    activation_state: str = "pending"
    approval_required: bool = True


@dataclass(frozen=True, slots=True)
class ApprovalPayload(_PayloadModel):
    candidate_id: str
    decision: str
    decision_reason: str
    approver: str
    decided_at: str


@dataclass(frozen=True, slots=True)
class ActivationPayload(_PayloadModel):
    candidate_id: str
    activation_id: str
    activated_by: str
    activated_at: str
    active_artifact_ref: str
    rollback_plan: str


@dataclass(frozen=True, slots=True)
class WorkerQualityPayload(_PayloadModel):
    worker_id: str
    score_delta: int
    reason_codes: list[str]
    source_event_ids: list[str]
    expires_at: str
