from __future__ import annotations

import hashlib
import json
from typing import Any

from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent, normalize


class V4WorkflowEngine:
    def __init__(self, *, event_store: EventStore):
        self._events = event_store

    def start_crew(self, *, crew_id: str, goal: str) -> AgentEvent:
        existing = self._events.get_by_idempotency_key(f"{crew_id}/crew.started")
        if existing is not None:
            return self._validate_crew_goal(existing, goal=goal)

        event = self._events.append(
            stream_id=crew_id,
            type="crew.started",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/crew.started",
            payload={"goal": goal},
        )
        return self._validate_crew_goal(event, goal=goal)

    def require_human(
        self,
        *,
        crew_id: str,
        reason: str,
        evidence_refs: list[str] | None = None,
    ) -> AgentEvent:
        artifact_refs = list(evidence_refs or [])
        payload = {"reason": reason}
        digest = _content_digest(payload=payload, artifact_refs=artifact_refs)
        return self._events.append(
            stream_id=crew_id,
            type="human.required",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/human.required/{reason}/{digest}",
            payload=payload,
            artifact_refs=artifact_refs,
        )

    def mark_ready(self, *, crew_id: str, round_id: str, evidence_refs: list[str]) -> AgentEvent:
        artifact_refs = list(evidence_refs)
        payload = {"round_id": round_id}
        digest = _content_digest(payload=payload, artifact_refs=artifact_refs)
        return self._events.append(
            stream_id=crew_id,
            type="crew.ready_for_accept",
            crew_id=crew_id,
            round_id=round_id,
            idempotency_key=f"{crew_id}/{round_id}/ready/{digest}",
            payload=payload,
            artifact_refs=artifact_refs,
        )

    def _validate_crew_goal(self, event: AgentEvent, *, goal: str) -> AgentEvent:
        if event.payload.get("goal") != goal:
            raise ValueError("crew already started with different goal")
        return event


def _content_digest(*, payload: dict[str, Any], artifact_refs: list[str]) -> str:
    content = json.dumps(
        normalize({"payload": payload, "artifact_refs": artifact_refs}),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
