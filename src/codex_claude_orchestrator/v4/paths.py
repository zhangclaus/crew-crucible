"""Canonical filesystem paths for V4 runtime artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class V4Paths:
    repo_root: Path
    crew_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_root", Path(self.repo_root))
        object.__setattr__(self, "crew_id", _safe_id(self.crew_id, "crew_id"))

    @property
    def state_root(self) -> Path:
        return self.repo_root / ".orchestrator"

    @property
    def crew_root(self) -> Path:
        return self.state_root / "crews" / self.crew_id

    @property
    def artifact_root(self) -> Path:
        return self.crew_root / "artifacts" / "v4"

    def worker_root(self, worker_id: str) -> Path:
        return self.artifact_root / "workers" / _safe_id(worker_id, "worker_id")

    def inbox_path(self, worker_id: str, message_id: str) -> Path:
        return (
            self.worker_root(worker_id)
            / "inbox"
            / f"{_safe_id(message_id, 'message_id')}.json"
        )

    def outbox_path(self, worker_id: str, turn_id: str) -> Path:
        return (
            self.worker_root(worker_id)
            / "outbox"
            / f"{_safe_id(turn_id, 'turn_id')}.json"
        )

    def patch_path(self, worker_id: str, turn_id: str) -> Path:
        return (
            self.worker_root(worker_id)
            / "patches"
            / f"{_safe_id(turn_id, 'turn_id')}.patch"
        )

    def changes_path(self, worker_id: str, turn_id: str) -> Path:
        return (
            self.worker_root(worker_id)
            / "changes"
            / f"{_safe_id(turn_id, 'turn_id')}.json"
        )

    def result_path(self, worker_id: str, turn_id: str) -> Path:
        return (
            self.worker_root(worker_id)
            / "results"
            / f"{_safe_id(turn_id, 'turn_id')}.json"
        )

    def merge_path(self, name: str) -> Path:
        return self.artifact_root / "merge" / f"{_safe_id(name, 'name')}.json"

    def projection_path(self, name: str) -> Path:
        return self.artifact_root / "projections" / f"{_safe_id(name, 'name')}.json"

    @property
    def learning_root(self) -> Path:
        return self.artifact_root / "learning"

    def learning_note_path(self, note_id: str) -> Path:
        return self.learning_root / "notes" / f"{_safe_id(note_id, 'note_id')}.json"

    def skill_candidate_path(self, candidate_id: str) -> Path:
        return (
            self.learning_root
            / "skill_candidates"
            / f"{_safe_id(candidate_id, 'candidate_id')}.json"
        )

    def guardrail_candidate_path(self, candidate_id: str) -> Path:
        return (
            self.learning_root
            / "guardrail_candidates"
            / f"{_safe_id(candidate_id, 'candidate_id')}.json"
        )

    @property
    def worker_quality_path(self) -> Path:
        return self.learning_root / "worker_quality.json"


def _safe_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or value == ".":
        raise ValueError(f"{field_name} is unsafe")

    if (
        Path(value).is_absolute()
        or ".." in value
        or "/" in value
        or "\\" in value
        or ":" in value
    ):
        raise ValueError(f"{field_name} is unsafe")

    return value


__all__ = ["V4Paths"]
