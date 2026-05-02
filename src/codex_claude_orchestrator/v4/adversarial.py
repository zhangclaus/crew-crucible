from __future__ import annotations

import hashlib
import json
from typing import Any

from codex_claude_orchestrator.v4.adversarial_models import (
    ChallengeIssuePayload,
    ChallengeSeverity,
    RepairCompletedPayload,
    RepairOutcome,
    RepairRequestPayload,
    WorkerPolicy,
)
from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent, normalize


def _digest(value: dict[str, Any]) -> str:
    content = json.dumps(normalize(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class AdversarialEvaluator:
    def __init__(self, *, event_store: EventStore) -> None:
        self._events = event_store

    def evaluate_completed_turn(self, completed_event: AgentEvent) -> AgentEvent:
        if completed_event.type != "turn.completed":
            raise ValueError("AdversarialEvaluator requires a turn.completed event")

        evidence = [
            event
            for event in self._events.list_by_turn(completed_event.turn_id)
            if event.crew_id == completed_event.crew_id
        ]
        if self._has_passed_verification(evidence):
            return self._append_pass_review(completed_event)
        return self._append_missing_verification_challenge(completed_event)

    def _has_passed_verification(self, evidence: list[AgentEvent]) -> bool:
        for event in evidence:
            if event.type == "verification.passed":
                return True
            if event.type != "worker.outbox.detected":
                continue
            if event.payload.get("valid") is not True:
                continue
            verification = event.payload.get("verification", [])
            if not isinstance(verification, list):
                continue
            if any(isinstance(item, dict) and item.get("status") == "passed" for item in verification):
                return True
        return False

    def _append_missing_verification_challenge(self, source: AgentEvent) -> AgentEvent:
        payload = ChallengeIssuePayload(
            challenge_id=f"challenge-{source.event_id}",
            source_turn_id=source.turn_id,
            source_event_ids=[source.event_id],
            severity=ChallengeSeverity.BLOCK,
            category="missing_verification",
            finding="Completed turn does not include passed verification evidence.",
            required_response=(
                "Repair the turn by adding or running relevant verification and writing a valid repair outbox."
            ),
            repair_allowed=True,
            artifact_refs=list(source.artifact_refs),
        ).to_payload()
        return self._append_evaluation_event(
            source=source,
            event_type="challenge.issued",
            payload=payload,
            artifact_refs=list(source.artifact_refs),
        )

    def _append_pass_review(self, source: AgentEvent) -> AgentEvent:
        payload = {
            "verdict": "pass",
            "source_event_ids": [source.event_id],
        }
        return self._append_evaluation_event(
            source=source,
            event_type="review.completed",
            payload=payload,
            artifact_refs=list(source.artifact_refs),
        )

    def _append_evaluation_event(
        self,
        *,
        source: AgentEvent,
        event_type: str,
        payload: dict[str, Any],
        artifact_refs: list[str],
    ) -> AgentEvent:
        return self._events.append(
            stream_id=source.crew_id,
            type=event_type,
            crew_id=source.crew_id,
            worker_id=source.worker_id,
            turn_id=source.turn_id,
            round_id=source.round_id,
            contract_id=source.contract_id,
            idempotency_key=self._idempotency_key(
                source=source,
                event_type=event_type,
                payload=payload,
                artifact_refs=artifact_refs,
            ),
            payload=payload,
            artifact_refs=artifact_refs,
        )

    def _idempotency_key(
        self,
        *,
        source: AgentEvent,
        event_type: str,
        payload: dict[str, Any],
        artifact_refs: list[str],
    ) -> str:
        digest = _digest(
            {
                "source_event_id": source.event_id,
                "event_type": event_type,
                "payload": payload,
                "artifact_refs": artifact_refs,
            }
        )
        return f"{source.crew_id}/{source.turn_id}/{event_type}/{digest}"


class ChallengeManager:
    def __init__(self, *, event_store: EventStore) -> None:
        self._events = event_store

    def request_repair(
        self,
        challenge_event: AgentEvent,
        *,
        repair_contract_id: str,
        repair_turn_id: str,
        worker_policy: WorkerPolicy | str,
        allowed_write_scope: list[str],
        acceptance_criteria: list[str],
        required_outbox_path: str,
    ) -> AgentEvent:
        if challenge_event.type != "challenge.issued":
            raise ValueError("repair can only be requested from challenge.issued")
        if challenge_event.payload.get("repair_allowed") is False:
            raise ValueError("challenge does not allow repair")

        challenge_id = str(challenge_event.payload.get("challenge_id", ""))
        payload = RepairRequestPayload(
            challenge_id=challenge_id,
            repair_contract_id=repair_contract_id,
            repair_turn_id=repair_turn_id,
            worker_policy=WorkerPolicy(worker_policy),
            allowed_write_scope=list(allowed_write_scope),
            acceptance_criteria=list(acceptance_criteria),
            required_outbox_path=required_outbox_path,
        ).to_payload()
        return self._events.append(
            stream_id=challenge_event.crew_id,
            type="repair.requested",
            crew_id=challenge_event.crew_id,
            worker_id=challenge_event.worker_id,
            turn_id=repair_turn_id,
            round_id=challenge_event.round_id,
            contract_id=repair_contract_id,
            idempotency_key=self._idempotency_key(
                crew_id=challenge_event.crew_id,
                challenge_id=challenge_id,
                event_type="repair.requested",
                payload=payload,
                context={
                    "worker_id": challenge_event.worker_id,
                    "turn_id": repair_turn_id,
                    "round_id": challenge_event.round_id,
                    "contract_id": repair_contract_id,
                },
            ),
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
        outcome: RepairOutcome | str,
        verification_event_ids: list[str],
        changed_files: list[str],
        summary: str = "",
    ) -> AgentEvent:
        artifact_refs = list(changed_files)
        payload = RepairCompletedPayload(
            challenge_id=challenge_id,
            repair_contract_id=contract_id,
            repair_turn_id=repair_turn_id,
            outcome=RepairOutcome(outcome),
            summary=summary,
            verification_event_ids=list(verification_event_ids),
            artifact_refs=artifact_refs,
        ).to_payload()
        return self._events.append(
            stream_id=crew_id,
            type="repair.completed",
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=repair_turn_id,
            round_id=round_id,
            contract_id=contract_id,
            idempotency_key=self._idempotency_key(
                crew_id=crew_id,
                challenge_id=challenge_id,
                event_type="repair.completed",
                payload=payload,
                context={
                    "worker_id": worker_id,
                    "turn_id": repair_turn_id,
                    "round_id": round_id,
                    "contract_id": contract_id,
                },
            ),
            payload=payload,
            artifact_refs=artifact_refs,
        )

    def _idempotency_key(
        self,
        *,
        crew_id: str,
        challenge_id: str,
        event_type: str,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        digest = _digest(
            {
                "payload": payload,
                "context": context,
            }
        )
        return f"{crew_id}/{challenge_id}/{event_type}/{digest}"
