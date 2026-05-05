from __future__ import annotations

import hashlib
import json
from typing import Any

from codex_claude_orchestrator.crew.gates import GateResult
from codex_claude_orchestrator.crew.readiness import ReadinessReport
from codex_claude_orchestrator.crew.review_verdict import ReviewVerdict
from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent, normalize


class GateEventBuilder:
    def __init__(self, event_store: EventStore | None = None) -> None:
        self.event_store = event_store

    def scope_evaluated(self, *, crew_id: str, round_id: str, worker_id: str, result: GateResult) -> AgentEvent:
        return self._build_event(
            crew_id=crew_id,
            round_id=round_id,
            worker_id=worker_id,
            event_type="scope.evaluated",
            payload={"round_id": round_id, **result.to_dict()},
            artifact_refs=list(result.evidence_refs),
        )

    def review_verdict(self, *, crew_id: str, round_id: str, worker_id: str, verdict: ReviewVerdict) -> AgentEvent:
        return self._build_event(
            crew_id=crew_id,
            round_id=round_id,
            worker_id=worker_id,
            event_type="review.verdict",
            payload={"round_id": round_id, **verdict.to_dict()},
            artifact_refs=list(verdict.evidence_refs),
        )

    def readiness_evaluated(self, *, crew_id: str, round_id: str, worker_id: str, report: ReadinessReport) -> AgentEvent:
        return self._build_event(
            crew_id=crew_id,
            round_id=round_id,
            worker_id=worker_id,
            event_type="readiness.evaluated",
            payload={**report.to_dict(), "round_id": round_id, "worker_id": worker_id},
            artifact_refs=list(report.evidence_refs),
        )

    def _build_event(
        self,
        *,
        crew_id: str,
        round_id: str,
        worker_id: str,
        event_type: str,
        payload: dict[str, Any],
        artifact_refs: list[str],
    ) -> AgentEvent:
        idempotency_key = self._idempotency_key(
            crew_id=crew_id,
            round_id=round_id,
            worker_id=worker_id,
            event_type=event_type,
            payload=payload,
            artifact_refs=artifact_refs,
        )

        if self.event_store is not None:
            return self.event_store.append(
                stream_id=crew_id,
                type=event_type,
                crew_id=crew_id,
                worker_id=worker_id,
                idempotency_key=idempotency_key,
                payload=payload,
                artifact_refs=artifact_refs,
            )

        digest = idempotency_key.rsplit("/", 1)[-1]
        return AgentEvent(
            event_id=f"event-{crew_id}-{round_id}-{event_type}-{digest}",
            stream_id=f"detached/{crew_id}/gates/{round_id}/{worker_id}/{event_type}",
            sequence=1,
            type=event_type,
            crew_id=crew_id,
            worker_id=worker_id,
            idempotency_key=idempotency_key,
            payload=payload,
            artifact_refs=artifact_refs,
        )

    def _idempotency_key(
        self,
        *,
        crew_id: str,
        round_id: str,
        worker_id: str,
        event_type: str,
        payload: dict[str, Any],
        artifact_refs: list[str],
    ) -> str:
        digest = self._content_digest(payload=payload, artifact_refs=artifact_refs)
        return f"gate/{crew_id}/{round_id}/{worker_id}/{event_type}/{digest}"

    def _content_digest(self, *, payload: dict[str, Any], artifact_refs: list[str]) -> str:
        content = json.dumps(
            normalize({"payload": payload, "artifact_refs": artifact_refs}),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
