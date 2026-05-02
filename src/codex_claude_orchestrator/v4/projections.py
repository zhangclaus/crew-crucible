from __future__ import annotations

from dataclasses import dataclass, field

from codex_claude_orchestrator.v4.events import AgentEvent
from codex_claude_orchestrator.v4.learning_projection import LearningProjection

_TERMINAL_CREW_STATUSES = {"ready", "needs_human", "accepted"}


@dataclass(slots=True)
class TurnProjection:
    turn_id: str
    worker_id: str
    status: str
    last_event_type: str


@dataclass(slots=True)
class CrewProjection:
    crew_id: str = ""
    goal: str = ""
    status: str = "empty"
    turns: dict[str, TurnProjection] = field(default_factory=dict)
    learning: LearningProjection = field(default_factory=LearningProjection)

    @classmethod
    def from_events(cls, events: list[AgentEvent]) -> "CrewProjection":
        projection = cls(learning=LearningProjection.from_events(events))
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
        if projection.learning.has_blocking_challenge and projection.status != "accepted":
            projection.status = "needs_human"
        return projection
