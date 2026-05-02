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
        open_challenges: dict[str, str] = {}
        candidate_states: dict[str, str] = {}
        active_skill_refs: list[str] = []
        active_guardrail_refs: list[str] = []
        worker_quality_scores: dict[str, int] = {}

        for event in events:
            payload = event.payload
            if event.type == "challenge.issued":
                challenge_id = str(payload.get("challenge_id", ""))
                if challenge_id:
                    open_challenges[challenge_id] = str(payload.get("severity", ""))
                continue

            if event.type == "repair.completed" and payload.get("outcome") == "fixed":
                challenge_id = str(payload.get("challenge_id", ""))
                if challenge_id:
                    open_challenges.pop(challenge_id, None)
                continue

            if event.type.endswith(".candidate_created"):
                candidate_id = str(payload.get("candidate_id", ""))
                if candidate_id:
                    candidate_states[candidate_id] = "pending"
                continue

            if event.type.endswith(".approved"):
                candidate_id = str(payload.get("candidate_id", ""))
                if candidate_id:
                    candidate_states[candidate_id] = "approved"
                continue

            if event.type.endswith(".rejected"):
                candidate_id = str(payload.get("candidate_id", ""))
                if candidate_id:
                    candidate_states[candidate_id] = "rejected"
                continue

            if event.type in {"skill.activated", "guardrail.activated"}:
                candidate_id = str(payload.get("candidate_id", ""))
                if candidate_id:
                    candidate_states[candidate_id] = "activated"
                artifact_ref = str(payload.get("active_artifact_ref", ""))
                if event.type == "skill.activated":
                    _append_unique(active_skill_refs, artifact_ref)
                else:
                    _append_unique(active_guardrail_refs, artifact_ref)
                continue

            if event.type == "worker.quality_updated":
                worker_id = str(payload.get("worker_id") or event.worker_id)
                if worker_id:
                    worker_quality_scores[worker_id] = worker_quality_scores.get(
                        worker_id, 0
                    ) + int(payload.get("score_delta", 0))

        return cls(
            open_challenge_ids=list(open_challenges),
            has_blocking_challenge=any(
                severity == "block" for severity in open_challenges.values()
            ),
            candidate_states=candidate_states,
            active_skill_refs=active_skill_refs,
            active_guardrail_refs=active_guardrail_refs,
            worker_quality_scores=worker_quality_scores,
        )


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


__all__ = ["LearningProjection"]
