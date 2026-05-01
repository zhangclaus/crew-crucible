from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from codex_claude_orchestrator.crew.models import (
    ProtocolRequest,
    ProtocolRequestStatus,
    is_terminal_protocol_request_status,
)
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.core.models import utc_now


class ProtocolRequestStore:
    def __init__(
        self,
        recorder: CrewRecorder,
        *,
        request_id_factory: Callable[[], str] | None = None,
    ):
        self._recorder = recorder
        self._request_id_factory = request_id_factory or (lambda: f"req-{uuid4().hex}")

    def create(
        self,
        *,
        crew_id: str,
        request_type: str,
        sender: str,
        recipient: str,
        subject: str,
        body: str = "",
        artifact_refs: list[str] | None = None,
        request_id: str | None = None,
    ) -> ProtocolRequest:
        request = ProtocolRequest(
            request_id=request_id or self._request_id_factory(),
            crew_id=crew_id,
            type=request_type,
            sender=sender,
            recipient=recipient,
            status=ProtocolRequestStatus.PENDING,
            subject=subject,
            body=body,
            artifact_refs=artifact_refs or [],
        )
        self._recorder.append_protocol_request(crew_id, request)
        return request

    def transition(
        self,
        *,
        crew_id: str,
        request_id: str,
        status: ProtocolRequestStatus | str,
        reason: str = "",
    ) -> ProtocolRequest:
        current = self.latest(crew_id, request_id)
        if current is None:
            raise FileNotFoundError(f"protocol request not found: {request_id}")
        if is_terminal_protocol_request_status(current["status"]):
            raise ValueError(f"terminal protocol request cannot transition: {request_id}")
        request = ProtocolRequest(
            request_id=current["request_id"],
            crew_id=current["crew_id"],
            type=current["type"],
            sender=current["from"],
            recipient=current["to"],
            status=ProtocolRequestStatus(status),
            subject=current["subject"],
            body=current.get("body", ""),
            reason=reason,
            artifact_refs=current.get("artifact_refs", []),
            created_at=current["created_at"],
            updated_at=utc_now(),
        )
        self._recorder.append_protocol_request(crew_id, request)
        return request

    def latest(self, crew_id: str, request_id: str) -> dict | None:
        matches = [item for item in self.list_requests(crew_id) if item["request_id"] == request_id]
        return matches[-1] if matches else None

    def list_requests(self, crew_id: str) -> list[dict]:
        return self._recorder.read_jsonl_stream(crew_id, "protocol_requests.jsonl")
