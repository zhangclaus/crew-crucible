"""Materializes full crew state from EventStore events.

CrewStateProjection replays all domain events for a crew and produces
a dict matching the shape of ``CrewRecorder.read_crew()`` output, enabling
the event store to serve as the single source of truth for read paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from codex_claude_orchestrator.v4.events import AgentEvent


@dataclass
class CrewStateProjection:
    """Materializes full crew state from EventStore events.

    Produces the same dict shape as CrewRecorder.read_crew().
    """

    crew: dict[str, Any] = field(default_factory=dict)
    tasks: list[dict] = field(default_factory=list)
    workers: list[dict] = field(default_factory=list)
    blackboard: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    worker_contracts: list[dict] = field(default_factory=list)
    known_pitfalls: list[dict] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    challenges: list[dict] = field(default_factory=list)
    verifications: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)

    @classmethod
    def from_events(cls, events: list[AgentEvent]) -> CrewStateProjection:
        proj = cls()
        for event in events:
            proj._apply(event)
        return proj

    def _apply(self, event: AgentEvent) -> None:
        # Store all events as generic event records
        self.events.append(
            {
                "event_id": event.event_id,
                "type": event.type,
                "crew_id": event.crew_id,
                "worker_id": event.worker_id,
                "turn_id": event.turn_id,
                "created_at": event.created_at,
            }
        )

        match event.type:
            case "crew.started":
                self.crew = {
                    "crew_id": event.crew_id,
                    "root_goal": event.payload.get("goal", ""),
                    "repo": event.payload.get("repo", ""),
                    "status": "running",
                    "created_at": event.created_at,
                    "updated_at": event.created_at,
                }
            case "crew.updated":
                self.crew.update(event.payload)
                self.crew["updated_at"] = event.created_at
            case "crew.stopped":
                self.crew["status"] = "cancelled"
                self.crew["ended_at"] = event.created_at
            case "crew.finalized":
                self.crew["status"] = event.payload.get("status", "accepted")
                self.crew["final_summary"] = event.payload.get("final_summary", "")
                self.crew["ended_at"] = event.created_at
            case "crew.accepted":
                self.crew["status"] = "accepted"
                self.crew["final_summary"] = event.payload.get("summary", "")
                self.crew["ended_at"] = event.created_at
            case "crew.ready_for_accept":
                if self.crew.get("status") not in ("accepted", "cancelled", "failed"):
                    self.crew["status"] = "ready"
            case "human.required":
                if self.crew.get("status") not in ("accepted", "cancelled", "failed"):
                    self.crew["status"] = "needs_human"
            case "worker.spawned":
                self.workers.append(
                    {
                        "worker_id": event.worker_id,
                        "crew_id": event.crew_id,
                        "role": event.payload.get("role", ""),
                        "status": "running",
                        "workspace_path": event.payload.get("workspace_path", ""),
                        "created_at": event.created_at,
                        "updated_at": event.created_at,
                        **{
                            k: v
                            for k, v in event.payload.items()
                            if k not in ("role", "workspace_path")
                        },
                    }
                )
            case "worker.contract.recorded":
                self.worker_contracts.append(
                    {
                        "contract_id": event.contract_id
                        or event.payload.get("contract_id", ""),
                        "label": event.payload.get("label", ""),
                        "mission": event.payload.get("mission", ""),
                        **{
                            k: v
                            for k, v in event.payload.items()
                            if k not in ("label", "mission")
                        },
                    }
                )
            case "worker.claimed":
                self._update_worker(
                    event.worker_id,
                    {"status": "busy", "updated_at": event.created_at},
                )
            case "worker.released":
                self._update_worker(
                    event.worker_id,
                    {"status": "idle", "updated_at": event.created_at},
                )
            case "worker.stopped":
                self._update_worker(
                    event.worker_id,
                    {"status": "stopped", "updated_at": event.created_at},
                )
            case "blackboard.entry":
                entry_id = _extract_trailing_id(event.idempotency_key)
                self.blackboard.append(
                    {
                        "entry_id": entry_id,
                        "crew_id": event.crew_id,
                        "type": event.payload.get("entry_type", ""),
                        "content": event.payload.get("content", ""),
                        "created_at": event.created_at,
                        **{
                            k: v
                            for k, v in event.payload.items()
                            if k not in ("entry_type", "content")
                        },
                    }
                )
            case "decision.recorded":
                action_id = _extract_trailing_id(event.idempotency_key)
                self.decisions.append(
                    {
                        "action_id": action_id,
                        "crew_id": event.crew_id,
                        "action_type": event.payload.get("action_type", ""),
                        "reason": event.payload.get("reason", ""),
                        "created_at": event.created_at,
                        **{
                            k: v
                            for k, v in event.payload.items()
                            if k not in ("action_type", "reason")
                        },
                    }
                )
            case "task.created":
                task_id = _extract_trailing_id(event.idempotency_key)
                self.tasks.append(
                    {
                        "task_id": task_id,
                        "crew_id": event.crew_id,
                        "title": event.payload.get("title", ""),
                        "status": "pending",
                        "created_at": event.created_at,
                        "updated_at": event.created_at,
                        **{k: v for k, v in event.payload.items() if k != "title"},
                    }
                )
            case "artifact.written":
                name = event.payload.get("artifact_name", "")
                if name and name not in self.artifacts:
                    self.artifacts.append(name)
            case "pitfall.recorded":
                self.known_pitfalls.append(
                    {
                        "failure_class": event.payload.get("failure_class", ""),
                        "summary": event.payload.get("summary", ""),
                        "guardrail": event.payload.get("guardrail", ""),
                        "created_at": event.created_at,
                        **{
                            k: v
                            for k, v in event.payload.items()
                            if k not in ("failure_class", "summary", "guardrail")
                        },
                    }
                )
            case "verification.passed":
                self.verifications.append(
                    {
                        "worker_id": event.worker_id,
                        "round_id": event.round_id,
                        "command": event.payload.get("command", ""),
                        "passed": True,
                        "created_at": event.created_at,
                    }
                )
            case "verification.failed":
                self.verifications.append(
                    {
                        "worker_id": event.worker_id,
                        "round_id": event.round_id,
                        "command": event.payload.get("command", ""),
                        "passed": False,
                        "created_at": event.created_at,
                    }
                )
            case "challenge.issued":
                self.challenges.append(
                    {
                        "worker_id": event.worker_id,
                        "round_id": event.round_id,
                        "finding": event.payload.get("finding", ""),
                        "category": event.payload.get("category", ""),
                        "severity": event.payload.get("severity", ""),
                        "created_at": event.created_at,
                    }
                )
            case "repair.requested":
                self.challenges.append(
                    {
                        "worker_id": event.worker_id,
                        "round_id": event.round_id,
                        "instruction": event.payload.get("instruction", ""),
                        "category": "repair",
                        "created_at": event.created_at,
                    }
                )
            case "review.completed":
                self.reviews.append(
                    {
                        "worker_id": event.worker_id,
                        "turn_id": event.turn_id,
                        "status": event.payload.get("status", ""),
                        "summary": event.payload.get("summary", ""),
                        "created_at": event.created_at,
                    }
                )

    def _update_worker(self, worker_id: str, updates: dict) -> None:
        for worker in self.workers:
            if worker.get("worker_id") == worker_id:
                worker.update(updates)
                return

    def to_read_crew_dict(self) -> dict[str, Any]:
        """Return dict matching CrewRecorder.read_crew() shape."""
        return {
            "crew": self.crew,
            "tasks": self.tasks,
            "workers": self.workers,
            "blackboard": self.blackboard,
            "events": self.events,
            "decisions": self.decisions,
            "worker_contracts": self.worker_contracts,
            "messages": [],
            "protocol_requests": [],
            "known_pitfalls": self.known_pitfalls,
            "message_cursors": {},
            "team_snapshot": None,
            "final_report": {
                "crew_id": self.crew.get("crew_id", ""),
                "status": self.crew.get("status", ""),
                "final_summary": self.crew.get("final_summary", ""),
                "ended_at": self.crew.get("ended_at", ""),
            }
            if self.crew.get("ended_at")
            else None,
            "artifacts": self.artifacts,
            "challenges": self.challenges,
            "verifications": self.verifications,
            "reviews": self.reviews,
        }

    def has_events(self) -> bool:
        """Return True if at least one crew.started event was applied."""
        return bool(self.crew)


def _extract_trailing_id(idempotency_key: str) -> str:
    """Extract the last segment of a ``/``-delimited idempotency key."""
    if "/" in idempotency_key:
        return idempotency_key.rsplit("/", 1)[-1]
    return idempotency_key


__all__ = ["CrewStateProjection"]
