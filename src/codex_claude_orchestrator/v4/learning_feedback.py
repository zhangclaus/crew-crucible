"""Automatic governed-learning feedback for repeated V4 failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re

from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent
from codex_claude_orchestrator.v4.learning import (
    GuardrailMemory,
    LearningRecorder,
    WorkerQualityTracker,
)
from codex_claude_orchestrator.v4.paths import V4Paths


@dataclass(frozen=True, slots=True)
class FeedbackPolicy:
    failure_class: str
    score_delta: int
    lesson: str
    guardrail_summary: str
    enforcement_point: str
    trigger_conditions: list[str]


_POLICIES = {
    "review_block": FeedbackPolicy(
        failure_class="repeated_review_block",
        score_delta=-2,
        lesson=(
            "A source worker repeatedly produced changes that were blocked by "
            "patch review before verification."
        ),
        guardrail_summary=(
            "Escalate source-worker output after repeated blocking review verdicts "
            "for the same worker."
        ),
        enforcement_point="v4.review",
        trigger_conditions=["challenge.issued.category=review_block", "count>=2"],
    ),
    "verification_failed": FeedbackPolicy(
        failure_class="repeated_verification_failed",
        score_delta=-3,
        lesson=(
            "A source worker repeatedly reached verification with failing checks "
            "after review."
        ),
        guardrail_summary=(
            "Escalate or constrain source-worker output after repeated verification "
            "failures for the same worker."
        ),
        enforcement_point="v4.verification",
        trigger_conditions=["challenge.issued.category=verification_failed", "count>=2"],
    ),
}


class GovernedLearningFeedback:
    def __init__(
        self,
        *,
        event_store: EventStore,
        paths: V4Paths,
        threshold: int = 2,
    ) -> None:
        if threshold < 2:
            raise ValueError("threshold must be at least 2")
        self._events = event_store
        self._paths = paths
        self._threshold = threshold
        self._notes = LearningRecorder(event_store=event_store, paths=paths)
        self._guardrails = GuardrailMemory(event_store=event_store, paths=paths)
        self._quality = WorkerQualityTracker(event_store=event_store, paths=paths)

    def record_challenge(self, challenge_event: AgentEvent) -> list[AgentEvent]:
        if challenge_event.type != "challenge.issued":
            return []

        category = str(challenge_event.payload.get("category", ""))
        policy = _POLICIES.get(category)
        if policy is None or not challenge_event.worker_id:
            return []

        repeated = self._matching_challenges(
            worker_id=challenge_event.worker_id,
            category=category,
        )
        if len(repeated) < self._threshold:
            return []

        source_events = repeated[: self._threshold]
        source_event_ids = [event.event_id for event in source_events]
        source_challenge_ids = [_challenge_id(event) for event in source_events]
        identity = f"{policy.failure_class}-{_slug(challenge_event.worker_id)}"
        note_id = f"note-{identity}"
        guardrail_id = f"guardrail-{identity}"
        lesson = self._lesson(policy=policy, source_events=source_events)

        note = self._notes.create_note(
            note_id=note_id,
            source_challenge_ids=source_challenge_ids,
            source_event_ids=source_event_ids,
            failure_class=policy.failure_class,
            lesson=lesson,
            trigger_conditions=policy.trigger_conditions,
            scope=f"crew:{self._paths.crew_id}/worker:{challenge_event.worker_id}",
        )
        guardrail = self._guardrails.create_candidate(
            candidate_id=guardrail_id,
            source_note_ids=[note_id],
            source_event_ids=[note.event_id, *source_event_ids],
            rule_summary=policy.guardrail_summary,
            enforcement_point=policy.enforcement_point,
            trigger_conditions=policy.trigger_conditions,
        )
        quality = self._quality.update_quality(
            worker_id=challenge_event.worker_id,
            score_delta=policy.score_delta,
            reason_codes=[policy.failure_class],
            source_event_ids=source_event_ids,
            expires_at=_quality_expires_at(source_events[0]),
        )
        return [note, guardrail, quality]

    def _matching_challenges(self, *, worker_id: str, category: str) -> list[AgentEvent]:
        return [
            event
            for event in self._events.list_stream(self._paths.crew_id)
            if event.type == "challenge.issued"
            and event.worker_id == worker_id
            and event.payload.get("category") == category
        ]

    @staticmethod
    def _lesson(*, policy: FeedbackPolicy, source_events: list[AgentEvent]) -> str:
        findings = [
            str(event.payload.get("finding", "")).strip()
            for event in source_events
            if str(event.payload.get("finding", "")).strip()
        ]
        if not findings:
            return policy.lesson
        return f"{policy.lesson} Repeated findings: {'; '.join(findings)}"


def _challenge_id(event: AgentEvent) -> str:
    challenge_id = str(event.payload.get("challenge_id", ""))
    return challenge_id or event.event_id


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return slug or "unknown"


def _quality_expires_at(source_event: AgentEvent) -> str:
    if source_event.created_at:
        try:
            created_at = datetime.fromisoformat(source_event.created_at.replace("Z", "+00:00"))
            return (created_at + timedelta(days=30)).astimezone(UTC).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return "9999-12-31T00:00:00Z"


__all__ = ["GovernedLearningFeedback"]
