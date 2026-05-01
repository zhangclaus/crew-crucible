from __future__ import annotations

import json
from collections.abc import Callable
from uuid import uuid4

from codex_claude_orchestrator.crew.models import AgentMessage, AgentMessageType
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder


class AgentMessageBus:
    def __init__(
        self,
        recorder: CrewRecorder,
        *,
        message_id_factory: Callable[[], str] | None = None,
        thread_id_factory: Callable[[], str] | None = None,
    ):
        self._recorder = recorder
        self._message_id_factory = message_id_factory or (lambda: f"msg-{uuid4().hex}")
        self._thread_id_factory = thread_id_factory or (lambda: f"thread-{uuid4().hex}")

    def send(
        self,
        *,
        crew_id: str,
        sender: str,
        recipient: str,
        message_type: AgentMessageType | str,
        body: str,
        artifact_refs: list[str] | None = None,
        request_id: str | None = None,
        requires_response: bool = False,
        thread_id: str | None = None,
    ) -> AgentMessage:
        message = AgentMessage(
            message_id=self._message_id_factory(),
            thread_id=thread_id or self._thread_id_factory(),
            request_id=request_id,
            crew_id=crew_id,
            sender=sender,
            recipient=recipient,
            type=AgentMessageType(message_type),
            body=body,
            artifact_refs=artifact_refs or [],
            requires_response=requires_response,
        )
        self.append(message)
        return message

    def append(self, message: AgentMessage) -> None:
        self._recorder.append_message(message.crew_id, message)

    def append_many(self, messages: list[AgentMessage]) -> None:
        for message in messages:
            self.append(message)

    def list_messages(self, crew_id: str) -> list[dict]:
        return self._recorder.read_jsonl_stream(crew_id, "messages.jsonl")

    def read_inbox(self, *, crew_id: str, recipient: str, mark_read: bool = False) -> list[dict]:
        messages = [message for message in self.list_messages(crew_id) if self._is_delivered_to(message, recipient)]
        cursor = self.cursor_summary(crew_id).get(recipient, 0)
        unread = messages[cursor:]
        if mark_read:
            cursors = self.cursor_summary(crew_id)
            cursors[recipient] = len(messages)
            self._write_cursors(crew_id, cursors)
        return unread

    def cursor_summary(self, crew_id: str) -> dict[str, int]:
        path = self._recorder._crew_dir(crew_id) / "message_cursors.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_cursors(self, crew_id: str, cursors: dict[str, int]) -> None:
        path = self._recorder._crew_dir(crew_id) / "message_cursors.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cursors, indent=2, ensure_ascii=False), encoding="utf-8")

    def _is_delivered_to(self, message: dict, recipient: str) -> bool:
        return message.get("to") in {recipient, "broadcast"}


def parse_codex_message_blocks(
    snapshot: str,
    *,
    crew_id: str,
    sender: str,
    message_id_factory: Callable[[], str] | None = None,
    thread_id_factory: Callable[[], str] | None = None,
) -> list[AgentMessage]:
    message_id_factory = message_id_factory or (lambda: f"msg-{uuid4().hex}")
    thread_id_factory = thread_id_factory or (lambda: f"thread-{uuid4().hex}")
    blocks = _extract_blocks(snapshot)
    messages: list[AgentMessage] = []
    for block in blocks:
        fields = _parse_block_fields(block)
        raw_type = fields.get("type", "status")
        try:
            message_type = AgentMessageType(raw_type)
        except ValueError as exc:
            raise ValueError(f"unsupported CODEX_MESSAGE type: {raw_type}") from exc
        artifact_refs = [
            item.strip()
            for item in fields.get("artifact_refs", "").split(",")
            if item.strip()
        ]
        metadata = {
            key: value
            for key, value in fields.items()
            if key
            not in {
                "to",
                "type",
                "body",
                "artifact_refs",
                "thread_id",
                "request_id",
                "requires_response",
            }
        }
        messages.append(
            AgentMessage(
                message_id=message_id_factory(),
                thread_id=fields.get("thread_id") or thread_id_factory(),
                request_id=fields.get("request_id") or None,
                crew_id=crew_id,
                sender=sender,
                recipient=fields.get("to", "codex"),
                type=message_type,
                body=fields.get("body", "").strip(),
                artifact_refs=artifact_refs,
                requires_response=_parse_bool(fields.get("requires_response", "false")),
                metadata=metadata,
            )
        )
    return messages


def _extract_blocks(snapshot: str) -> list[str]:
    blocks: list[str] = []
    marker = "<<<CODEX_MESSAGE"
    end_marker = ">>>"
    cursor = 0
    while True:
        start = snapshot.find(marker, cursor)
        if start < 0:
            return blocks
        body_start = snapshot.find("\n", start)
        if body_start < 0:
            return blocks
        end = snapshot.find(end_marker, body_start)
        if end < 0:
            return blocks
        blocks.append(snapshot[body_start + 1 : end])
        cursor = end + len(end_marker)


def _parse_block_fields(block: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    active_key: str | None = None
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line:
            if active_key == "body":
                fields["body"] = f"{fields.get('body', '')}\n"
            continue
        if ":" in line and not line.startswith((" ", "\t")):
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            fields[key] = value
            active_key = key
            continue
        if active_key == "body":
            fields["body"] = f"{fields.get('body', '')}\n{line.strip()}".strip("\n")
    return fields


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}
