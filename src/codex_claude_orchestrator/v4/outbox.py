"""Worker outbox result parsing for V4 turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass(frozen=True, slots=True)
class WorkerOutboxResult:
    crew_id: str = ""
    worker_id: str = ""
    turn_id: str = ""
    status: str = ""
    summary: str = ""
    changed_files: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    verification: list[Any] = field(default_factory=list)
    review: dict[str, Any] = field(default_factory=dict)
    acknowledged_message_ids: list[str] = field(default_factory=list)
    messages: list[Any] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next_suggested_action: str = ""
    validation_errors: list[str] = field(default_factory=list)

    VALID_STATUSES: ClassVar[set[str]] = {"completed", "blocked", "failed", "inconclusive"}
    STRING_LIST_FIELDS: ClassVar[tuple[str, ...]] = (
        "changed_files",
        "artifact_refs",
        "acknowledged_message_ids",
        "risks",
    )
    LIST_FIELDS: ClassVar[tuple[str, ...]] = (
        "verification",
        "messages",
    )

    @property
    def is_valid(self) -> bool:
        return not self.validation_errors

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkerOutboxResult":
        if not isinstance(payload, dict):
            raise TypeError("WorkerOutboxResult payload must be a dict")

        errors: list[str] = []
        crew_id = _required_string(payload, "crew_id", errors)
        worker_id = _required_string(payload, "worker_id", errors)
        turn_id = _required_string(payload, "turn_id", errors)
        status = _required_string(payload, "status", errors)
        if status and status not in cls.VALID_STATUSES:
            valid_statuses = ", ".join(sorted(cls.VALID_STATUSES))
            errors.append(f"status must be one of: {valid_statuses}")

        string_lists = {
            field_name: _string_list(payload, field_name, errors)
            for field_name in cls.STRING_LIST_FIELDS
        }
        lists = {field_name: _list(payload, field_name, errors) for field_name in cls.LIST_FIELDS}
        review = _dict(payload, "review", errors)
        summary = payload.get("summary", "")
        if not isinstance(summary, str):
            summary = ""
        next_suggested_action = payload.get("next_suggested_action", "")
        if not isinstance(next_suggested_action, str):
            next_suggested_action = ""

        return cls(
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=turn_id,
            status=status,
            summary=summary,
            changed_files=string_lists["changed_files"],
            artifact_refs=string_lists["artifact_refs"],
            verification=lists["verification"],
            review=review,
            acknowledged_message_ids=string_lists["acknowledged_message_ids"],
            messages=lists["messages"],
            risks=string_lists["risks"],
            next_suggested_action=next_suggested_action,
            validation_errors=errors,
        )


def _required_string(payload: dict[str, Any], field_name: str, errors: list[str]) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field_name} is required")
        return ""
    return value


def _string_list(payload: dict[str, Any], field_name: str, errors: list[str]) -> list[str]:
    value = payload.get(field_name, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        errors.append(f"{field_name} must be a list of strings")
        return []
    return value


def _list(payload: dict[str, Any], field_name: str, errors: list[str]) -> list[Any]:
    value = payload.get(field_name, [])
    if not isinstance(value, list):
        errors.append(f"{field_name} must be a list")
        return []
    return value


def _dict(payload: dict[str, Any], field_name: str, errors: list[str]) -> dict[str, Any]:
    value = payload.get(field_name, {})
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be a dict")
        return {}
    return value
