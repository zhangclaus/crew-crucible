"""Runtime adapter models for V4 worker orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from codex_claude_orchestrator.v4.events import normalize


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    crew_id: str
    worker_id: str
    runtime_type: str
    contract_id: str
    workspace_path: str = ""
    terminal_pane: str = ""
    transcript_artifact: str = ""
    capabilities: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.runtime_type:
            raise ValueError("runtime_type is required")


@dataclass(frozen=True, slots=True)
class WorkerHandle:
    crew_id: str
    worker_id: str
    runtime_type: str
    status: str = "running"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnEnvelope:
    crew_id: str
    worker_id: str
    turn_id: str
    round_id: str
    phase: str
    message: str
    expected_marker: str
    contract_id: str = ""
    completion_mode: str = "structured_required"
    requires_structured_result: bool = True
    deadline_at: str = ""
    attempt: int = 1

    @property
    def idempotency_key(self) -> str:
        return f"{self.crew_id}/{self.worker_id}/{self.turn_id}/{self.phase}"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: bool
    marker: str
    reason: str = ""
    artifact_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    type: str
    turn_id: str
    worker_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    artifact_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return normalize(self)


@dataclass(frozen=True, slots=True)
class CancellationResult:
    cancelled: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class StopResult:
    stopped: bool
    reason: str = ""


class RuntimeAdapter(Protocol):
    def spawn_worker(self, spec: WorkerSpec) -> WorkerHandle:
        ...

    def deliver_turn(self, turn: TurnEnvelope) -> DeliveryResult:
        ...

    def watch_turn(self, turn: TurnEnvelope) -> Iterable[RuntimeEvent]:
        ...

    def collect_artifacts(self, turn: TurnEnvelope) -> list[str]:
        ...

    def cancel_turn(self, turn: TurnEnvelope) -> CancellationResult:
        ...

    def stop_worker(self, worker_id: str) -> StopResult:
        ...
