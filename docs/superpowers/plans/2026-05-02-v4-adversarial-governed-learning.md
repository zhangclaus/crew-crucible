# V4 Adversarial Governed Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the V4 adversarial evaluation and governed learning phase on top of the unmerged V4 event-native foundation.

**Architecture:** Build this as an event-native product layer that consumes existing V4 turn, outbox, review, verification, and gate evidence. New services append replayable events through the existing `EventStore` protocol; local learning artifacts live under the canonical V4 artifact root. Approval and activation are separate states so pending or approved-but-not-activated learning artifacts cannot affect prompts, planner scoring, worker reuse, or deterministic gates.

**Tech Stack:** Python 3.11+, pytest, stdlib dataclasses/enums/json/pathlib/hashlib, existing `codex_claude_orchestrator.v4` foundation branch `codex/v4-event-native-runtime` at commit `38dbc5b`.

---

## Scope Check

This plan implements only the adversarial evaluation and governed learning phase described in `docs/superpowers/specs/2026-05-02-v4-event-native-agent-filesystem-design.md`. It assumes the V4 foundation branch is either checked out at `.worktrees/v4-event-native-runtime` or merged before implementation begins.

This plan does not implement the merge transaction, dirty-base protection, full planner upgrade, or CLI main-path migration. It records events and projections that those later phases will consume.

## File Structure

- Create: `src/codex_claude_orchestrator/v4/adversarial_models.py`
  Owns typed payload models and enums for challenge, repair, learning, approval, activation, and worker quality events.
- Create: `src/codex_claude_orchestrator/v4/adversarial.py`
  Owns `AdversarialEvaluator` and `ChallengeManager`. It inspects durable evidence, emits `review.completed`, `challenge.issued`, `challenge.answered`, `repair.requested`, and `repair.completed`.
- Create: `src/codex_claude_orchestrator/v4/learning.py`
  Owns `LearningRecorder`, `SkillCandidateGate`, `GuardrailMemory`, and `WorkerQualityTracker`. It writes local artifacts and appends learning/approval/activation/quality events.
- Create: `src/codex_claude_orchestrator/v4/learning_projection.py`
  Replays V4 events into open challenges, repair status, candidate status, active artifacts, and worker quality.
- Modify: `src/codex_claude_orchestrator/v4/paths.py`
  Add canonical learning artifact path helpers.
- Modify: `src/codex_claude_orchestrator/v4/projections.py`
  Surface unresolved challenges and learning state in crew projections without changing existing turn projection behavior.
- Modify: `src/codex_claude_orchestrator/v4/supervisor.py`
  Add optional adversarial workflow hook after `turn.completed`; leave the default behavior compatible when no evaluator is configured.
- Tests:
  - Create: `tests/v4/test_adversarial_models.py`
  - Create: `tests/v4/test_adversarial_evaluator.py`
  - Create: `tests/v4/test_challenge_manager.py`
  - Create: `tests/v4/test_learning.py`
  - Create: `tests/v4/test_learning_projection.py`
  - Modify: `tests/v4/test_paths.py`
  - Modify: `tests/v4/test_projections.py`
  - Modify: `tests/v4/test_supervisor.py`

## Task 1: Typed Payload Models

**Files:**
- Create: `src/codex_claude_orchestrator/v4/adversarial_models.py`
- Test: `tests/v4/test_adversarial_models.py`

- [ ] **Step 1: Write failing tests for payload serialization**

Create `tests/v4/test_adversarial_models.py`:

```python
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
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_adversarial_models.py -q
```

Expected: FAIL because `codex_claude_orchestrator.v4.adversarial_models` does not exist.

- [ ] **Step 3: Implement typed payload models**

Create `src/codex_claude_orchestrator/v4/adversarial_models.py`:

```python
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
    answer_turn_id: str
    status: str
    summary: str
    evidence_event_ids: list[str] = field(default_factory=list)


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
    repair_turn_id: str
    outcome: RepairOutcome
    verification_event_ids: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)


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
```

- [ ] **Step 4: Run focused tests and verify green**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_adversarial_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/adversarial_models.py tests/v4/test_adversarial_models.py
git commit -m "feat: add v4 adversarial payload models"
```

## Task 2: Canonical Learning Artifact Paths

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/paths.py`
- Test: `tests/v4/test_paths.py`

- [ ] **Step 1: Write failing tests for learning paths**

Append to `tests/v4/test_paths.py`:

```python
def test_v4_paths_include_learning_artifacts(tmp_path: Path) -> None:
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")

    assert paths.learning_root == paths.artifact_root / "learning"
    assert paths.learning_note_path("note-1") == paths.artifact_root / "learning" / "notes" / "note-1.json"
    assert paths.skill_candidate_path("skill-1") == paths.artifact_root / "learning" / "skill_candidates" / "skill-1.json"
    assert paths.guardrail_candidate_path("guardrail-1") == paths.artifact_root / "learning" / "guardrail_candidates" / "guardrail-1.json"
    assert paths.worker_quality_path == paths.artifact_root / "learning" / "worker_quality.json"


def test_v4_learning_paths_reject_unsafe_ids(tmp_path: Path) -> None:
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")

    with pytest.raises(ValueError, match="unsafe"):
        paths.learning_note_path("../note")
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_paths.py -q
```

Expected: FAIL because `learning_root`, `learning_note_path`, `skill_candidate_path`, `guardrail_candidate_path`, and `worker_quality_path` do not exist.

- [ ] **Step 3: Add learning path helpers**

Modify `src/codex_claude_orchestrator/v4/paths.py`:

```python
    @property
    def learning_root(self) -> Path:
        return self.artifact_root / "learning"

    def learning_note_path(self, note_id: str) -> Path:
        return self.learning_root / "notes" / f"{self._safe_id(note_id)}.json"

    def skill_candidate_path(self, candidate_id: str) -> Path:
        return self.learning_root / "skill_candidates" / f"{self._safe_id(candidate_id)}.json"

    def guardrail_candidate_path(self, candidate_id: str) -> Path:
        return self.learning_root / "guardrail_candidates" / f"{self._safe_id(candidate_id)}.json"

    @property
    def worker_quality_path(self) -> Path:
        return self.learning_root / "worker_quality.json"
```

Use the existing `V4Paths._safe_id()` helper so candidate ids cannot escape the canonical artifact root.

- [ ] **Step 4: Run focused tests and verify green**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_paths.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/paths.py tests/v4/test_paths.py
git commit -m "feat: add v4 learning artifact paths"
```

## Task 3: Adversarial Evaluator

**Files:**
- Create: `src/codex_claude_orchestrator/v4/adversarial.py`
- Test: `tests/v4/test_adversarial_evaluator.py`

- [ ] **Step 1: Write failing evaluator tests**

Create `tests/v4/test_adversarial_evaluator.py`:

```python
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
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_adversarial_evaluator.py -q
```

Expected: FAIL because `AdversarialEvaluator` does not exist.

- [ ] **Step 3: Implement evaluator**

Create `src/codex_claude_orchestrator/v4/adversarial.py` with this evaluator skeleton:

```python
"""Adversarial evaluation workflow for completed V4 turns."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from codex_claude_orchestrator.v4.adversarial_models import (
    ChallengeIssuePayload,
    ChallengeSeverity,
)
from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent, normalize


class AdversarialEvaluator:
    def __init__(self, *, event_store: EventStore):
        self._events = event_store

    def evaluate_completed_turn(self, completed_event: AgentEvent) -> AgentEvent:
        if completed_event.type != "turn.completed":
            raise ValueError("AdversarialEvaluator requires a turn.completed event")

        evidence = self._events.list_by_turn(completed_event.turn_id)
        if self._has_passed_verification(evidence):
            return self._append_review(completed_event, verdict="pass", summary="Verification evidence is present.")

        challenge = ChallengeIssuePayload(
            challenge_id=f"challenge-{uuid4().hex}",
            source_turn_id=completed_event.turn_id,
            source_event_ids=[completed_event.event_id],
            severity=ChallengeSeverity.BLOCK,
            category="missing_verification",
            finding="Completed turn does not include passed verification evidence.",
            required_response="Repair the turn by adding or running relevant verification and writing a valid repair outbox.",
            repair_allowed=True,
            artifact_refs=list(completed_event.artifact_refs),
        )
        return self._append_payload_event(
            event_type="challenge.issued",
            source=completed_event,
            payload=challenge.to_payload(),
            artifact_refs=challenge.artifact_refs,
        )

    def _append_review(self, source: AgentEvent, *, verdict: str, summary: str) -> AgentEvent:
        payload = {
            "verdict": verdict,
            "summary": summary,
            "source_turn_id": source.turn_id,
            "source_event_ids": [source.event_id],
        }
        return self._append_payload_event(
            event_type="review.completed",
            source=source,
            payload=payload,
            artifact_refs=list(source.artifact_refs),
        )

    def _has_passed_verification(self, evidence: list[AgentEvent]) -> bool:
        if any(event.type == "verification.passed" for event in evidence):
            return True
        for event in evidence:
            if event.type != "worker.outbox.detected":
                continue
            verification = event.payload.get("verification", [])
            if isinstance(verification, list) and any(
                isinstance(item, dict) and item.get("status") == "passed"
                for item in verification
            ):
                return True
        return False

    def _append_payload_event(
        self,
        *,
        event_type: str,
        source: AgentEvent,
        payload: dict,
        artifact_refs: list[str],
    ) -> AgentEvent:
        digest = _content_digest(payload=payload, artifact_refs=artifact_refs)
        return self._events.append(
            stream_id=source.crew_id,
            type=event_type,
            crew_id=source.crew_id,
            worker_id=source.worker_id,
            turn_id=source.turn_id,
            round_id=source.round_id,
            contract_id=source.contract_id,
            idempotency_key=f"{source.crew_id}/{source.turn_id}/{event_type}/{digest}",
            payload=payload,
            artifact_refs=artifact_refs,
        )


def _content_digest(*, payload: dict, artifact_refs: list[str]) -> str:
    content = json.dumps(
        normalize({"payload": payload, "artifact_refs": artifact_refs}),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
```

Keep the first evaluator narrow. Later tasks add repair, learning, and projection behavior.

- [ ] **Step 4: Run focused tests and verify green**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_adversarial_evaluator.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/adversarial.py tests/v4/test_adversarial_evaluator.py
git commit -m "feat: add v4 adversarial evaluator"
```

## Task 4: Challenge Manager and Repair Events

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/adversarial.py`
- Test: `tests/v4/test_challenge_manager.py`

- [ ] **Step 1: Write failing challenge manager tests**

Create `tests/v4/test_challenge_manager.py`:

```python
from codex_claude_orchestrator.v4.adversarial import ChallengeManager
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore


def test_challenge_manager_requests_repair_from_challenge_event(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    challenge = store.append(
        stream_id="crew-1",
        type="challenge.issued",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
        payload={
            "challenge_id": "challenge-1",
            "source_turn_id": "turn-1",
            "source_event_ids": ["evt-source"],
            "severity": "block",
            "category": "missing_verification",
            "finding": "No verification.",
            "required_response": "Repair with verification.",
            "repair_allowed": True,
            "artifact_refs": ["workers/worker-1/outbox/turn-1.json"],
        },
    )

    manager = ChallengeManager(event_store=store)
    event = manager.request_repair(
        challenge,
        repair_contract_id="contract-repair-1",
        repair_turn_id="turn-repair-1",
        worker_policy="fresh_worker",
        allowed_write_scope=["src/**/*.py", "tests/**/*.py"],
        acceptance_criteria=["Repair includes passed verification."],
        required_outbox_path="workers/worker-2/outbox/turn-repair-1.json",
    )

    assert event.type == "repair.requested"
    assert event.payload["challenge_id"] == "challenge-1"
    assert event.payload["worker_policy"] == "fresh_worker"
    assert event.turn_id == "turn-repair-1"


def test_challenge_manager_records_repair_completion(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    manager = ChallengeManager(event_store=store)

    event = manager.complete_repair(
        crew_id="crew-1",
        worker_id="worker-2",
        round_id="round-1",
        contract_id="contract-repair-1",
        challenge_id="challenge-1",
        repair_turn_id="turn-repair-1",
        outcome="fixed",
        verification_event_ids=["evt-verification"],
        changed_files=["tests/test_feature.py"],
    )

    assert event.type == "repair.completed"
    assert event.payload["outcome"] == "fixed"
    assert event.payload["verification_event_ids"] == ["evt-verification"]
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_challenge_manager.py -q
```

Expected: FAIL because `ChallengeManager` does not exist.

- [ ] **Step 3: Implement challenge manager**

Append to `src/codex_claude_orchestrator/v4/adversarial.py`:

```python
from codex_claude_orchestrator.v4.adversarial_models import (
    RepairCompletedPayload,
    RepairOutcome,
    RepairRequestPayload,
    WorkerPolicy,
)


class ChallengeManager:
    def __init__(self, *, event_store: EventStore):
        self._events = event_store

    def request_repair(
        self,
        challenge_event: AgentEvent,
        *,
        repair_contract_id: str,
        repair_turn_id: str,
        worker_policy: str,
        allowed_write_scope: list[str],
        acceptance_criteria: list[str],
        required_outbox_path: str,
    ) -> AgentEvent:
        if challenge_event.type != "challenge.issued":
            raise ValueError("repair can only be requested from challenge.issued")
        challenge_id = str(challenge_event.payload["challenge_id"])
        payload = RepairRequestPayload(
            challenge_id=challenge_id,
            repair_contract_id=repair_contract_id,
            repair_turn_id=repair_turn_id,
            worker_policy=WorkerPolicy(worker_policy),
            allowed_write_scope=list(allowed_write_scope),
            acceptance_criteria=list(acceptance_criteria),
            required_outbox_path=required_outbox_path,
        ).to_payload()
        digest = _content_digest(payload=payload, artifact_refs=[])
        return self._events.append(
            stream_id=challenge_event.crew_id,
            type="repair.requested",
            crew_id=challenge_event.crew_id,
            worker_id=challenge_event.worker_id,
            turn_id=repair_turn_id,
            round_id=challenge_event.round_id,
            contract_id=repair_contract_id,
            idempotency_key=f"{challenge_event.crew_id}/{challenge_id}/repair.requested/{digest}",
            payload=payload,
        )

    def complete_repair(
        self,
        *,
        crew_id: str,
        worker_id: str,
        round_id: str,
        contract_id: str,
        challenge_id: str,
        repair_turn_id: str,
        outcome: str,
        verification_event_ids: list[str],
        changed_files: list[str],
    ) -> AgentEvent:
        payload = RepairCompletedPayload(
            challenge_id=challenge_id,
            repair_turn_id=repair_turn_id,
            outcome=RepairOutcome(outcome),
            verification_event_ids=list(verification_event_ids),
            changed_files=list(changed_files),
        ).to_payload()
        digest = _content_digest(payload=payload, artifact_refs=[])
        return self._events.append(
            stream_id=crew_id,
            type="repair.completed",
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=repair_turn_id,
            round_id=round_id,
            contract_id=contract_id,
            idempotency_key=f"{crew_id}/{challenge_id}/repair.completed/{digest}",
            payload=payload,
        )
```

- [ ] **Step 4: Run focused tests and verify green**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_challenge_manager.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/adversarial.py tests/v4/test_challenge_manager.py
git commit -m "feat: add v4 challenge repair events"
```

## Task 5: Learning Recorder and Candidate Gates

**Files:**
- Create: `src/codex_claude_orchestrator/v4/learning.py`
- Test: `tests/v4/test_learning.py`

- [ ] **Step 1: Write failing learning tests**

Create `tests/v4/test_learning.py`:

```python
import json

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
    assert json.loads(note_path.read_text(encoding="utf-8"))["lesson"] == "Repairs need passed verification evidence."


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
    assert json.loads(paths.worker_quality_path.read_text(encoding="utf-8"))["worker-1"]["score"] == -2
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_learning.py -q
```

Expected: FAIL because `codex_claude_orchestrator.v4.learning` does not exist.

- [ ] **Step 3: Implement learning services**

Create `src/codex_claude_orchestrator/v4/learning.py`:

```python
"""Governed learning artifacts and events for V4."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from codex_claude_orchestrator.v4.adversarial_models import (
    ActivationPayload,
    ApprovalPayload,
    CandidatePayload,
    LearningNotePayload,
    WorkerQualityPayload,
)
from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent, normalize
from codex_claude_orchestrator.v4.paths import V4Paths


class LearningRecorder:
    def __init__(self, *, event_store: EventStore, paths: V4Paths):
        self._events = event_store
        self._paths = paths

    def create_note(
        self,
        *,
        note_id: str,
        source_challenge_ids: list[str],
        source_event_ids: list[str],
        failure_class: str,
        lesson: str,
        trigger_conditions: list[str],
        scope: str,
    ) -> AgentEvent:
        payload = LearningNotePayload(
            note_id=note_id,
            source_challenge_ids=list(source_challenge_ids),
            source_event_ids=list(source_event_ids),
            failure_class=failure_class,
            lesson=lesson,
            trigger_conditions=list(trigger_conditions),
            scope=scope,
        ).to_payload()
        artifact_ref = f"learning/notes/{note_id}.json"
        _write_json(self._paths.learning_note_path(note_id), payload)
        return _append_event(
            self._events,
            crew_id=self._paths.crew_id,
            event_type="learning.note_created",
            payload=payload,
            artifact_refs=[artifact_ref],
        )
```

Continue in the same file with candidate services:

```python
class SkillCandidateGate:
    def __init__(self, *, event_store: EventStore, paths: V4Paths):
        self._events = event_store
        self._paths = paths

    def create_candidate(
        self,
        *,
        candidate_id: str,
        source_note_ids: list[str],
        source_event_ids: list[str],
        summary: str,
        trigger_conditions: list[str],
        body: str,
    ) -> AgentEvent:
        artifact_ref = f"learning/skill_candidates/{candidate_id}.json"
        payload = CandidatePayload(
            candidate_id=candidate_id,
            source_note_ids=list(source_note_ids),
            source_event_ids=list(source_event_ids),
            kind="skill",
            summary=summary,
            trigger_conditions=list(trigger_conditions),
            artifact_ref=artifact_ref,
        ).to_payload()
        _write_json(self._paths.skill_candidate_path(candidate_id), {**payload, "body": body})
        return _append_event(
            self._events,
            crew_id=self._paths.crew_id,
            event_type="skill.candidate_created",
            payload=payload,
            artifact_refs=[artifact_ref],
        )

    def approve_candidate(self, *, candidate_id: str, decision_reason: str, approver: str, decided_at: str) -> AgentEvent:
        return _candidate_decision(
            self._events,
            crew_id=self._paths.crew_id,
            event_type="skill.approved",
            candidate_id=candidate_id,
            decision="approved",
            decision_reason=decision_reason,
            approver=approver,
            decided_at=decided_at,
        )

    def reject_candidate(self, *, candidate_id: str, decision_reason: str, approver: str, decided_at: str) -> AgentEvent:
        return _candidate_decision(
            self._events,
            crew_id=self._paths.crew_id,
            event_type="skill.rejected",
            candidate_id=candidate_id,
            decision="rejected",
            decision_reason=decision_reason,
            approver=approver,
            decided_at=decided_at,
        )

    def activate_candidate(self, *, candidate_id: str, activation_id: str, activated_by: str, activated_at: str) -> AgentEvent:
        artifact_path = self._paths.skill_candidate_path(candidate_id)
        if not artifact_path.exists():
            raise FileNotFoundError(artifact_path)
        artifact_ref = f"learning/skill_candidates/{candidate_id}.json"
        payload = ActivationPayload(
            candidate_id=candidate_id,
            activation_id=activation_id,
            activated_by=activated_by,
            activated_at=activated_at,
            active_artifact_ref=artifact_ref,
            rollback_plan="Append a rejecting follow-up decision and stop injecting this active artifact ref.",
        ).to_payload()
        return _append_event(
            self._events,
            crew_id=self._paths.crew_id,
            event_type="skill.activated",
            payload=payload,
            artifact_refs=[artifact_ref],
        )
```

Add `GuardrailMemory` with the same method names as `SkillCandidateGate`, using event types `guardrail.candidate_created`, `guardrail.approved`, `guardrail.rejected`, and `guardrail.activated`, and using `paths.guardrail_candidate_path(candidate_id)`.

Add worker quality tracking:

```python
class WorkerQualityTracker:
    def __init__(self, *, event_store: EventStore, paths: V4Paths):
        self._events = event_store
        self._paths = paths

    def update_quality(
        self,
        *,
        worker_id: str,
        score_delta: int,
        reason_codes: list[str],
        source_event_ids: list[str],
        expires_at: str,
    ) -> AgentEvent:
        payload = WorkerQualityPayload(
            worker_id=worker_id,
            score_delta=score_delta,
            reason_codes=list(reason_codes),
            source_event_ids=list(source_event_ids),
            expires_at=expires_at,
        ).to_payload()
        state = _read_json(self._paths.worker_quality_path)
        current = state.get(worker_id, {"score": 0, "updates": []})
        current["score"] = int(current.get("score", 0)) + score_delta
        current.setdefault("updates", []).append(payload)
        state[worker_id] = current
        _write_json(self._paths.worker_quality_path, state)
        return _append_event(
            self._events,
            crew_id=self._paths.crew_id,
            worker_id=worker_id,
            event_type="worker.quality_updated",
            payload=payload,
        )
```

Add shared helpers:

```python
def _candidate_decision(
    event_store: EventStore,
    *,
    crew_id: str,
    event_type: str,
    candidate_id: str,
    decision: str,
    decision_reason: str,
    approver: str,
    decided_at: str,
) -> AgentEvent:
    payload = ApprovalPayload(
        candidate_id=candidate_id,
        decision=decision,
        decision_reason=decision_reason,
        approver=approver,
        decided_at=decided_at,
    ).to_payload()
    return _append_event(event_store, crew_id=crew_id, event_type=event_type, payload=payload)


def _append_event(
    event_store: EventStore,
    *,
    crew_id: str,
    event_type: str,
    payload: dict,
    artifact_refs: list[str] | None = None,
    worker_id: str = "",
) -> AgentEvent:
    refs = list(artifact_refs or [])
    digest = _content_digest(payload=payload, artifact_refs=refs)
    return event_store.append(
        stream_id=crew_id,
        type=event_type,
        crew_id=crew_id,
        worker_id=worker_id,
        idempotency_key=f"{crew_id}/{event_type}/{digest}",
        payload=payload,
        artifact_refs=refs,
    )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalize(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _content_digest(*, payload: dict, artifact_refs: list[str]) -> str:
    content = json.dumps(
        normalize({"payload": payload, "artifact_refs": artifact_refs}),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run focused tests and verify green**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_learning.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/learning.py tests/v4/test_learning.py
git commit -m "feat: add v4 governed learning events"
```

## Task 6: Learning Projection

**Files:**
- Create: `src/codex_claude_orchestrator/v4/learning_projection.py`
- Modify: `src/codex_claude_orchestrator/v4/projections.py`
- Test: `tests/v4/test_learning_projection.py`
- Test: `tests/v4/test_projections.py`

- [ ] **Step 1: Write failing projection tests**

Create `tests/v4/test_learning_projection.py`:

```python
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
```

Append to `tests/v4/test_projections.py`:

```python
def test_crew_projection_surfaces_learning_blockers(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(stream_id="crew-1", type="crew.started", crew_id="crew-1", payload={"goal": "Fix"})
    store.append(
        stream_id="crew-1",
        type="challenge.issued",
        crew_id="crew-1",
        payload={"challenge_id": "challenge-1", "severity": "block"},
    )

    projection = CrewProjection.from_events(store.list_stream("crew-1"))

    assert projection.status == "needs_human"
    assert projection.learning.has_blocking_challenge is True
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_learning_projection.py tests/v4/test_projections.py -q
```

Expected: FAIL because `LearningProjection` does not exist and `CrewProjection` has no `learning` field.

- [ ] **Step 3: Implement learning projection**

Create `src/codex_claude_orchestrator/v4/learning_projection.py`:

```python
"""Replay projections for V4 adversarial learning state."""

from __future__ import annotations

from dataclasses import dataclass, field

from codex_claude_orchestrator.v4.events import AgentEvent


@dataclass(slots=True)
class LearningProjection:
    open_challenge_ids: list[str] = field(default_factory=list)
    has_blocking_challenge: bool = False
    candidate_states: dict[str, str] = field(default_factory=dict)
    active_skill_refs: list[str] = field(default_factory=list)
    active_guardrail_refs: list[str] = field(default_factory=list)
    worker_quality_scores: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_events(cls, events: list[AgentEvent]) -> "LearningProjection":
        projection = cls()
        open_challenges: dict[str, str] = {}
        for event in events:
            if event.type == "challenge.issued":
                challenge_id = str(event.payload.get("challenge_id", ""))
                if challenge_id:
                    open_challenges[challenge_id] = str(event.payload.get("severity", "warn"))
            if event.type == "repair.completed" and event.payload.get("outcome") == "fixed":
                open_challenges.pop(str(event.payload.get("challenge_id", "")), None)
            if event.type.endswith(".candidate_created"):
                candidate_id = str(event.payload.get("candidate_id", ""))
                if candidate_id:
                    projection.candidate_states[candidate_id] = "pending"
            if event.type.endswith(".approved"):
                candidate_id = str(event.payload.get("candidate_id", ""))
                if candidate_id:
                    projection.candidate_states[candidate_id] = "approved"
            if event.type.endswith(".rejected"):
                candidate_id = str(event.payload.get("candidate_id", ""))
                if candidate_id:
                    projection.candidate_states[candidate_id] = "rejected"
            if event.type == "skill.activated":
                ref = str(event.payload.get("active_artifact_ref", ""))
                if ref:
                    projection.active_skill_refs.append(ref)
                    projection.candidate_states[str(event.payload.get("candidate_id", ""))] = "activated"
            if event.type == "guardrail.activated":
                ref = str(event.payload.get("active_artifact_ref", ""))
                if ref:
                    projection.active_guardrail_refs.append(ref)
                    projection.candidate_states[str(event.payload.get("candidate_id", ""))] = "activated"
            if event.type == "worker.quality_updated":
                worker_id = event.worker_id or str(event.payload.get("worker_id", ""))
                if worker_id:
                    projection.worker_quality_scores[worker_id] = (
                        projection.worker_quality_scores.get(worker_id, 0)
                        + int(event.payload.get("score_delta", 0))
                    )
        projection.open_challenge_ids = sorted(open_challenges)
        projection.has_blocking_challenge = any(severity == "block" for severity in open_challenges.values())
        return projection
```

Modify `CrewProjection` in `src/codex_claude_orchestrator/v4/projections.py`:

```python
from codex_claude_orchestrator.v4.learning_projection import LearningProjection


@dataclass(slots=True)
class CrewProjection:
    crew_id: str = ""
    goal: str = ""
    status: str = "empty"
    turns: dict[str, TurnProjection] = field(default_factory=dict)
    learning: LearningProjection = field(default_factory=LearningProjection)

    @classmethod
    def from_events(cls, events: list[AgentEvent]) -> "CrewProjection":
        projection = cls()
        projection.learning = LearningProjection.from_events(events)
        for event in events:
            if event.crew_id:
                if projection.crew_id and event.crew_id != projection.crew_id:
                    raise ValueError(
                        f"mixed crew ids in projection events: {projection.crew_id}, {event.crew_id}"
                    )
                projection.crew_id = event.crew_id
            if event.type == "crew.started":
                projection.status = "running"
                projection.goal = str(event.payload.get("goal", ""))
                continue
            if event.type.startswith("turn.") and event.turn_id:
                projection.turns[event.turn_id] = TurnProjection(
                    turn_id=event.turn_id,
                    worker_id=event.worker_id,
                    status=event.type.split(".", 1)[1],
                    last_event_type=event.type,
                )
                if projection.status not in _TERMINAL_CREW_STATUSES:
                    projection.status = "running"
            if event.type == "crew.ready_for_accept":
                projection.status = "ready"
            if event.type == "human.required":
                projection.status = "needs_human"
            if event.type == "crew.accepted":
                projection.status = "accepted"
        if projection.learning.has_blocking_challenge and projection.status not in {"accepted"}:
            projection.status = "needs_human"
        return projection
```

Do not remove the existing turn projection logic. Add the learning status adjustment just before returning the projection.

- [ ] **Step 4: Run focused tests and verify green**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_learning_projection.py tests/v4/test_projections.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/learning_projection.py \
        src/codex_claude_orchestrator/v4/projections.py \
        tests/v4/test_learning_projection.py \
        tests/v4/test_projections.py
git commit -m "feat: project v4 adversarial learning state"
```

## Task 7: Supervisor Hook

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/supervisor.py`
- Test: `tests/v4/test_supervisor.py`

- [ ] **Step 1: Write failing supervisor hook test**

Append to `tests/v4/test_supervisor.py`:

```python
class FakeAdversarialEvaluator:
    def __init__(self):
        self.completed_events = []

    def evaluate_completed_turn(self, completed_event):
        self.completed_events.append(completed_event)
        return completed_event


def test_v4_supervisor_invokes_adversarial_evaluator_after_turn_completed(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    evaluator = FakeAdversarialEvaluator()
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter(lambda turn: [completed_outbox_event(turn)]),
        adversarial_evaluator=evaluator,
    )

    supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert [event.type for event in evaluator.completed_events] == ["turn.completed"]
```

- [ ] **Step 2: Run test and verify red**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_supervisor.py::test_v4_supervisor_invokes_adversarial_evaluator_after_turn_completed -q
```

Expected: FAIL because `V4Supervisor.__init__()` does not accept `adversarial_evaluator`.

- [ ] **Step 3: Add optional evaluator hook**

Modify `V4Supervisor.__init__()`:

```python
    def __init__(
        self,
        *,
        event_store: SQLiteEventStore,
        artifact_store: ArtifactStore,
        adapter,
        turn_context_builder=None,
        adversarial_evaluator=None,
    ):
        self._events = event_store
        self._artifacts = artifact_store
        self._adapter = adapter
        self._turn_context_builder = turn_context_builder
        self._adversarial_evaluator = adversarial_evaluator
```

After the supervisor appends a `turn.completed` event, invoke the hook:

```python
        terminal_event = self._events.append(
            stream_id=crew_id,
            type=decision.event_type,
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=turn.turn_id,
            round_id=turn.round_id,
            contract_id=turn.contract_id,
            idempotency_key=f"{crew_id}/{turn.turn_id}/{decision.event_type}",
            payload={"reason": decision.reason},
            artifact_refs=decision.evidence_refs,
        )
        if terminal_event.type == "turn.completed" and self._adversarial_evaluator is not None:
            self._adversarial_evaluator.evaluate_completed_turn(terminal_event)
```

Keep existing result status unchanged so current callers still receive `{"crew_id": crew_id, "status": "turn_completed", "turn_id": turn.turn_id}`.

- [ ] **Step 4: Run focused tests and verify green**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_supervisor.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/supervisor.py tests/v4/test_supervisor.py
git commit -m "feat: hook v4 adversarial evaluation into supervisor"
```

## Task 8: End-to-End Challenge, Repair, and Learning Flow

**Files:**
- Test: `tests/v4/test_adversarial_workflow.py`
- Modify only files from previous tasks if this end-to-end test exposes integration gaps.

- [ ] **Step 1: Write failing end-to-end replay test**

Create `tests/v4/test_adversarial_workflow.py`:

```python
from codex_claude_orchestrator.v4.adversarial import AdversarialEvaluator, ChallengeManager
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.learning import LearningRecorder, SkillCandidateGate
from codex_claude_orchestrator.v4.learning_projection import LearningProjection
from codex_claude_orchestrator.v4.paths import V4Paths


def test_challenge_repair_learning_flow_replays_without_terminal_output(tmp_path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")

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
    store.append(
        stream_id="crew-1",
        type="worker.outbox.detected",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
        payload={"valid": True, "status": "completed", "verification": []},
    )

    challenge = AdversarialEvaluator(event_store=store).evaluate_completed_turn(completed)
    repair = ChallengeManager(event_store=store).complete_repair(
        crew_id="crew-1",
        worker_id="worker-2",
        round_id="round-1",
        contract_id="contract-repair-1",
        challenge_id=challenge.payload["challenge_id"],
        repair_turn_id="turn-repair-1",
        outcome="fixed",
        verification_event_ids=["evt-verification"],
        changed_files=["tests/test_feature.py"],
    )
    note = LearningRecorder(event_store=store, paths=paths).create_note(
        note_id="note-1",
        source_challenge_ids=[challenge.payload["challenge_id"]],
        source_event_ids=[challenge.event_id, repair.event_id],
        failure_class="missing_verification",
        lesson="Do not accept repair turns without passed verification evidence.",
        trigger_conditions=["repair turn", "missing verification"],
        scope="v4 readiness",
    )
    gate = SkillCandidateGate(event_store=store, paths=paths)
    gate.create_candidate(
        candidate_id="skill-1",
        source_note_ids=[note.payload["note_id"]],
        source_event_ids=[note.event_id],
        summary="Require passed verification evidence for repair turns.",
        trigger_conditions=["repair turn"],
        body="Check `verification.passed` or passed outbox verification before readiness.",
    )

    projection = LearningProjection.from_events(store.list_stream("crew-1"))

    assert projection.open_challenge_ids == []
    assert projection.has_blocking_challenge is False
    assert projection.candidate_states["skill-1"] == "pending"
    assert projection.active_skill_refs == []
```

- [ ] **Step 2: Run test and verify red or integration failure**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_adversarial_workflow.py -q
```

Expected: FAIL only if the previous tasks have an integration gap. If it passes immediately, continue to Step 4.

- [ ] **Step 3: Fix integration gaps with the smallest local change**

Use the failure message to adjust only the modules from this plan. Acceptable fixes are:

```python
# If projection does not close fixed challenges, make repair.completed with outcome fixed pop challenge_id.
if event.type == "repair.completed" and event.payload.get("outcome") == "fixed":
    open_challenges.pop(str(event.payload.get("challenge_id", "")), None)

# If candidate state is missing, ensure .candidate_created event families set pending state.
if event.type.endswith(".candidate_created"):
    projection.candidate_states[str(event.payload.get("candidate_id", ""))] = "pending"
```

- [ ] **Step 4: Run end-to-end test and verify green**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_adversarial_workflow.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/v4/test_adversarial_workflow.py \
        src/codex_claude_orchestrator/v4/adversarial.py \
        src/codex_claude_orchestrator/v4/learning.py \
        src/codex_claude_orchestrator/v4/learning_projection.py
git commit -m "test: cover v4 adversarial learning workflow"
```

## Task 9: Full Verification

**Files:**
- No planned source edits.
- Use failures to decide whether a narrow fix is needed.

- [ ] **Step 1: Run focused V4 suite**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4 -q
```

Expected: PASS.

- [ ] **Step 2: Run affected legacy suites**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/session tests/crew tests/cli/test_cli.py tests/ui/test_server.py -q
```

Expected: PASS. These suites protect existing V2/V3 challenge, skill, UI, and CLI behavior.

- [ ] **Step 3: Run full suite**

Run:

```bash
/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 4: Commit any verification-only compatibility fixes**

If Step 1-3 required narrow compatibility edits, commit them:

```bash
git add src tests
git commit -m "fix: preserve compatibility for v4 adversarial learning"
```

If there were no fixes, skip the commit and record the passing commands in the implementation summary.

## Self-Review

Spec coverage:

- Event-native challenge/review/repair events are covered by Tasks 1, 3, 4, and 8.
- Governed learning notes, skill candidates, guardrail candidates, approval, activation, and worker quality are covered by Tasks 1, 2, 5, and 6.
- The approval versus activation boundary is covered by Tasks 1, 5, 6, and 8.
- Replay/projection behavior is covered by Task 6.
- Supervisor integration is covered by Task 7.
- Terminal-output independence is covered by Task 8 because the flow uses only stored V4 events and artifacts.

Known gaps intentionally left for later plans:

- Merge transaction and dirty-base protection are not implemented here.
- Planner scoring and worker reuse changes are not implemented here; this plan only records active learning refs and worker quality scores for later consumers.
- CLI/UI commands for approving and activating V4 learning artifacts are not implemented here; this plan exposes event and projection primitives first.
