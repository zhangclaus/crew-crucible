"""Turn context assembly for V4 worker turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from codex_claude_orchestrator.crew.models import is_terminal_protocol_request_status


class InboxReader(Protocol):
    def read_inbox(self, *, crew_id: str, recipient: str, mark_read: bool = False) -> list[dict]:
        ...


class ProtocolRequestReader(Protocol):
    def list_requests(self, crew_id: str) -> list[dict]:
        ...


@dataclass(frozen=True, slots=True)
class TurnContext:
    crew_id: str
    worker_id: str
    unread_count: int
    unread_message_ids: list[str] = field(default_factory=list)
    unread_inbox_digest: str = ""
    open_protocol_requests: list[dict] = field(default_factory=list)
    open_protocol_requests_digest: str = ""


class TurnContextBuilder:
    def __init__(
        self,
        message_bus: InboxReader,
        *,
        protocol_request_store: ProtocolRequestReader | None = None,
    ):
        self._message_bus = message_bus
        self._protocol_request_store = protocol_request_store

    def build(self, *, crew_id: str, worker_id: str) -> TurnContext:
        unread = self._message_bus.read_inbox(
            crew_id=crew_id,
            recipient=worker_id,
            mark_read=False,
        )
        open_requests = self._open_protocol_requests(crew_id=crew_id, worker_id=worker_id)
        unread_message_ids = [
            message["message_id"]
            for message in unread
            if isinstance(message.get("message_id"), str)
        ]
        return TurnContext(
            crew_id=crew_id,
            worker_id=worker_id,
            unread_count=len(unread),
            unread_message_ids=unread_message_ids,
            unread_inbox_digest=_digest_messages(unread),
            open_protocol_requests=open_requests,
            open_protocol_requests_digest=_digest_protocol_requests(open_requests),
        )

    def _open_protocol_requests(self, *, crew_id: str, worker_id: str) -> list[dict]:
        if self._protocol_request_store is None:
            return []
        latest_by_id: dict[str, dict] = {}
        for request in self._protocol_request_store.list_requests(crew_id):
            if request.get("to") not in {worker_id, "broadcast"}:
                continue
            request_id = request.get("request_id")
            if isinstance(request_id, str) and request_id:
                latest_by_id[request_id] = request
        return [
            _protocol_request_summary(request)
            for request in latest_by_id.values()
            if not is_terminal_protocol_request_status(request.get("status", "pending"))
        ]


def _digest_messages(messages: list[dict]) -> str:
    lines = []
    for message in messages:
        message_id = _text(message.get("message_id"), "unknown-message")
        sender = _text(message.get("from"), "unknown-sender")
        message_type = _text(message.get("type"), "message")
        body = " ".join(_text(message.get("body"), "").split())
        if body:
            lines.append(f"- [{message_id}] {message_type} from {sender}: {body}")
        else:
            lines.append(f"- [{message_id}] {message_type} from {sender}")
    return "\n".join(lines)


def _digest_protocol_requests(requests: list[dict]) -> str:
    lines = []
    for request in requests:
        request_id = _text(request.get("request_id"), "unknown-request")
        request_type = _text(request.get("type"), "protocol")
        sender = _text(request.get("from"), "unknown-sender")
        subject = _text(request.get("subject"), "")
        body = " ".join(_text(request.get("body"), "").split())
        detail = f"{subject}: {body}" if subject and body else subject or body
        if detail:
            lines.append(f"- [{request_id}] {request_type} from {sender}: {detail}")
        else:
            lines.append(f"- [{request_id}] {request_type} from {sender}")
    return "\n".join(lines)


def _protocol_request_summary(request: dict) -> dict:
    return {
        "request_id": _text(request.get("request_id"), ""),
        "type": _text(request.get("type"), ""),
        "from": _text(request.get("from"), ""),
        "to": _text(request.get("to"), ""),
        "status": _text(request.get("status"), ""),
        "subject": _text(request.get("subject"), ""),
        "body": _text(request.get("body"), ""),
        "artifact_refs": request.get("artifact_refs") if isinstance(request.get("artifact_refs"), list) else [],
    }


def _text(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default
