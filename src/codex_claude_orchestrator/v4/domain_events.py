"""Typed domain event helpers that wrap EventStore.append().

DomainEventEmitter provides crew-level, worker-level, blackboard,
decision, task, artifact, and pitfall event emission with consistent
idempotency key conventions and payload normalization.
"""

from __future__ import annotations

import hashlib
from typing import Any

from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent, normalize


def _summary_hash(text: str) -> str:
    """Return a short hex digest for use in idempotency keys."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


class DomainEventEmitter:
    """Wraps EventStore.append() with typed domain event helpers.

    Each emit method calls ``self._events.append()`` with:
    - ``stream_id=crew_id`` (all domain events are per-crew)
    - ``type=<event_type>``
    - ``crew_id=crew_id``
    - ``worker_id=<if applicable>``
    - ``contract_id=<if applicable>``
    - ``idempotency_key=<convention>``
    - ``payload=<normalized data>``

    Idempotency key convention: ``{crew_id}/{event_type}/{entity_id}``

    Fire-and-forget: this class does not handle errors — callers are responsible.
    """

    def __init__(self, events: EventStore) -> None:
        self._events = events

    # -- Crew lifecycle ---------------------------------------------------

    def emit_crew_started(
        self,
        crew_id: str,
        goal: str,
        repo: str = "",
        **extra: Any,
    ) -> AgentEvent:
        payload: dict[str, Any] = {"goal": goal}
        if repo:
            payload["repo"] = repo
        payload.update(extra)
        return self._events.append(
            stream_id=crew_id,
            type="crew.started",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/crew.started",
            payload=normalize(payload),
        )

    def emit_crew_updated(
        self,
        crew_id: str,
        updates: dict[str, Any],
    ) -> AgentEvent:
        return self._events.append(
            stream_id=crew_id,
            type="crew.updated",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/crew.updated",
            payload=normalize(updates),
        )

    def emit_crew_stopped(
        self,
        crew_id: str,
        reason: str = "",
    ) -> AgentEvent:
        payload: dict[str, Any] = {}
        if reason:
            payload["reason"] = reason
        return self._events.append(
            stream_id=crew_id,
            type="crew.stopped",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/crew.stopped",
            payload=normalize(payload),
        )

    def emit_crew_finalized(
        self,
        crew_id: str,
        status: str,
        final_summary: str = "",
    ) -> AgentEvent:
        payload: dict[str, Any] = {"status": status}
        if final_summary:
            payload["final_summary"] = final_summary
        return self._events.append(
            stream_id=crew_id,
            type="crew.finalized",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/crew.finalized",
            payload=normalize(payload),
        )

    # -- Worker lifecycle -------------------------------------------------

    def emit_worker_spawned(
        self,
        crew_id: str,
        worker_id: str,
        role: str = "",
        workspace_path: str = "",
        **extra: Any,
    ) -> AgentEvent:
        payload: dict[str, Any] = {}
        if role:
            payload["role"] = role
        if workspace_path:
            payload["workspace_path"] = workspace_path
        payload.update(extra)
        return self._events.append(
            stream_id=crew_id,
            type="worker.spawned",
            crew_id=crew_id,
            worker_id=worker_id,
            idempotency_key=f"{crew_id}/worker.spawned/{worker_id}",
            payload=normalize(payload),
        )

    def emit_worker_contract_recorded(
        self,
        crew_id: str,
        contract_id: str,
        label: str = "",
        mission: str = "",
        **extra: Any,
    ) -> AgentEvent:
        payload: dict[str, Any] = {}
        if label:
            payload["label"] = label
        if mission:
            payload["mission"] = mission
        payload.update(extra)
        return self._events.append(
            stream_id=crew_id,
            type="worker.contract.recorded",
            crew_id=crew_id,
            contract_id=contract_id,
            idempotency_key=f"{crew_id}/worker.contract.recorded/{contract_id}",
            payload=normalize(payload),
        )

    def emit_worker_claimed(
        self,
        crew_id: str,
        worker_id: str,
    ) -> AgentEvent:
        return self._events.append(
            stream_id=crew_id,
            type="worker.claimed",
            crew_id=crew_id,
            worker_id=worker_id,
            idempotency_key=f"{crew_id}/worker.claimed/{worker_id}",
        )

    def emit_worker_released(
        self,
        crew_id: str,
        worker_id: str,
    ) -> AgentEvent:
        return self._events.append(
            stream_id=crew_id,
            type="worker.released",
            crew_id=crew_id,
            worker_id=worker_id,
            idempotency_key=f"{crew_id}/worker.released/{worker_id}",
        )

    def emit_worker_stopped(
        self,
        crew_id: str,
        worker_id: str,
    ) -> AgentEvent:
        return self._events.append(
            stream_id=crew_id,
            type="worker.stopped",
            crew_id=crew_id,
            worker_id=worker_id,
            idempotency_key=f"{crew_id}/worker.stopped/{worker_id}",
        )

    # -- Blackboard -------------------------------------------------------

    def emit_blackboard_entry(
        self,
        crew_id: str,
        entry_id: str,
        entry_type: str = "",
        content: str = "",
        **extra: Any,
    ) -> AgentEvent:
        payload: dict[str, Any] = {}
        if entry_type:
            payload["entry_type"] = entry_type
        if content:
            payload["content"] = content
        payload.update(extra)
        return self._events.append(
            stream_id=crew_id,
            type="blackboard.entry",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/blackboard/{entry_id}",
            payload=normalize(payload),
        )

    # -- Decisions --------------------------------------------------------

    def emit_decision_recorded(
        self,
        crew_id: str,
        action_id: str,
        action_type: str = "",
        reason: str = "",
        **extra: Any,
    ) -> AgentEvent:
        payload: dict[str, Any] = {}
        if action_type:
            payload["action_type"] = action_type
        if reason:
            payload["reason"] = reason
        payload.update(extra)
        return self._events.append(
            stream_id=crew_id,
            type="decision.recorded",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/decision/{action_id}",
            payload=normalize(payload),
        )

    # -- Tasks ------------------------------------------------------------

    def emit_task_created(
        self,
        crew_id: str,
        task_id: str,
        title: str = "",
        **extra: Any,
    ) -> AgentEvent:
        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title
        payload.update(extra)
        return self._events.append(
            stream_id=crew_id,
            type="task.created",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/task.created/{task_id}",
            payload=normalize(payload),
        )

    # -- Artifacts --------------------------------------------------------

    def emit_artifact_written(
        self,
        crew_id: str,
        artifact_name: str,
        sha256: str = "",
        **extra: Any,
    ) -> AgentEvent:
        payload: dict[str, Any] = {"artifact_name": artifact_name}
        if sha256:
            payload["sha256"] = sha256
        payload.update(extra)
        return self._events.append(
            stream_id=crew_id,
            type="artifact.written",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/artifact/{artifact_name}/{sha256}",
            payload=normalize(payload),
        )

    # -- Pitfalls ---------------------------------------------------------

    def emit_pitfall_recorded(
        self,
        crew_id: str,
        failure_class: str,
        summary: str = "",
        guardrail: str = "",
        **extra: Any,
    ) -> AgentEvent:
        summary_hash = _summary_hash(summary) if summary else ""
        payload: dict[str, Any] = {"failure_class": failure_class}
        if summary:
            payload["summary"] = summary
        if guardrail:
            payload["guardrail"] = guardrail
        payload.update(extra)
        return self._events.append(
            stream_id=crew_id,
            type="pitfall.recorded",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/pitfall/{failure_class}/{summary_hash}",
            payload=normalize(payload),
        )
