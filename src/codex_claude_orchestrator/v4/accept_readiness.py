"""Replay-only accept readiness gate for V4 crew streams."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent, normalize


_ACCEPTABLE_REVIEW_STATUSES = {"ok", "warn"}
_POST_READY_INVALIDATING_TYPES = {
    "human.required",
    "turn.failed",
    "turn.timeout",
    "turn.inconclusive",
    "verification.failed",
}


@dataclass(slots=True)
class AcceptReadinessDecision:
    allowed: bool
    reason: str
    round_id: str = ""
    ready_event_id: str = ""
    review_event_id: str = ""
    verification_event_ids: list[str] = field(default_factory=list)
    blocking_challenge_event_ids: list[str] = field(default_factory=list)
    invalidating_event_ids: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return normalize(
            {
                "allowed": self.allowed,
                "reason": self.reason,
                "round_id": self.round_id,
                "ready_event_id": self.ready_event_id,
                "review_event_id": self.review_event_id,
                "verification_event_ids": self.verification_event_ids,
                "blocking_challenge_event_ids": self.blocking_challenge_event_ids,
                "invalidating_event_ids": self.invalidating_event_ids,
            }
        )


class AcceptReadinessGate:
    def __init__(self, *, event_store: EventStore) -> None:
        self._events = event_store

    def evaluate(self, crew_id: str) -> AcceptReadinessDecision:
        events = self._events.list_stream(crew_id)
        ready_event = self._latest_ready_event(events)
        if ready_event is None:
            return AcceptReadinessDecision(
                allowed=False,
                reason="missing_ready_for_accept",
            )

        round_id = _event_round_id(ready_event)
        if not round_id:
            return AcceptReadinessDecision(
                allowed=False,
                reason="ready_round_missing",
                ready_event_id=ready_event.event_id,
            )

        through_ready = [
            event
            for event in events
            if event.sequence <= ready_event.sequence and _event_round_id(event) == round_id
        ]
        review_event = self._latest_acceptable_review(through_ready)
        if review_event is None:
            return AcceptReadinessDecision(
                allowed=False,
                reason="ready_round_missing_review",
                round_id=round_id,
                ready_event_id=ready_event.event_id,
            )

        verification_event_ids = [
            event.event_id for event in through_ready if event.type == "verification.passed"
        ]
        if not verification_event_ids:
            return AcceptReadinessDecision(
                allowed=False,
                reason="ready_round_missing_verification",
                round_id=round_id,
                ready_event_id=ready_event.event_id,
                review_event_id=review_event.event_id,
            )

        blocking_challenge_event_ids = [
            event.event_id
            for event in through_ready
            if event.type == "challenge.issued"
            and event.sequence > review_event.sequence
            and _is_blocking_challenge(event)
        ]
        if blocking_challenge_event_ids:
            return AcceptReadinessDecision(
                allowed=False,
                reason="blocking_challenge_open",
                round_id=round_id,
                ready_event_id=ready_event.event_id,
                review_event_id=review_event.event_id,
                verification_event_ids=verification_event_ids,
                blocking_challenge_event_ids=blocking_challenge_event_ids,
            )

        invalidating_event_ids = [
            event.event_id
            for event in events
            if event.sequence > ready_event.sequence
            and _event_round_id(event) == round_id
            and _is_post_ready_invalidating_event(event)
        ]
        if invalidating_event_ids:
            return AcceptReadinessDecision(
                allowed=False,
                reason="ready_invalidated_after_ready",
                round_id=round_id,
                ready_event_id=ready_event.event_id,
                review_event_id=review_event.event_id,
                verification_event_ids=verification_event_ids,
                invalidating_event_ids=invalidating_event_ids,
            )

        return AcceptReadinessDecision(
            allowed=True,
            reason="ready",
            round_id=round_id,
            ready_event_id=ready_event.event_id,
            review_event_id=review_event.event_id,
            verification_event_ids=verification_event_ids,
        )

    def _latest_ready_event(self, events: list[AgentEvent]) -> AgentEvent | None:
        for event in reversed(events):
            if event.type == "crew.ready_for_accept":
                return event
        return None

    def _latest_acceptable_review(self, events: list[AgentEvent]) -> AgentEvent | None:
        for event in reversed(events):
            if event.type != "review.completed":
                continue
            if event.payload.get("status") in _ACCEPTABLE_REVIEW_STATUSES:
                return event
        return None


def _event_round_id(event: AgentEvent) -> str:
    round_id = event.round_id or event.payload.get("round_id", "")
    return str(round_id) if round_id else ""


def _is_blocking_challenge(event: AgentEvent) -> bool:
    return str(event.payload.get("severity") or "block") == "block"


def _is_post_ready_invalidating_event(event: AgentEvent) -> bool:
    if event.type in _POST_READY_INVALIDATING_TYPES:
        return True
    return event.type == "challenge.issued" and _is_blocking_challenge(event)
