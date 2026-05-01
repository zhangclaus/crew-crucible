from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.core.models import WorkspaceMode, utc_now


def _normalize(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {item.name: _normalize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {key: _normalize(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_normalize(inner) for inner in value]
    return value


class CrewStatus(StrEnum):
    PLANNING = "planning"
    RUNNING = "running"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"
    ACCEPTED = "accepted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerRole(StrEnum):
    EXPLORER = "explorer"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"


class WorkerStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    IDLE = "idle"
    FAILED = "failed"
    STOPPED = "stopped"


class CrewTaskStatus(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    SUBMITTED = "submitted"
    CHALLENGED = "challenged"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class BlackboardEntryType(StrEnum):
    FACT = "fact"
    CLAIM = "claim"
    QUESTION = "question"
    RISK = "risk"
    PATCH = "patch"
    VERIFICATION = "verification"
    REVIEW = "review"
    DECISION = "decision"


class ActorType(StrEnum):
    CODEX = "codex"
    WORKER = "worker"


class AuthorityLevel(StrEnum):
    READONLY = "readonly"
    SOURCE_WRITE = "source_write"
    TEST_WRITE = "test_write"
    STATE_WRITE = "state_write"


class WorkspacePolicy(StrEnum):
    READONLY = "readonly"
    WORKTREE = "worktree"
    SHARED = "shared"


class DecisionActionType(StrEnum):
    SPAWN_WORKER = "spawn_worker"
    SEND_WORKER = "send_worker"
    OBSERVE_WORKER = "observe_worker"
    ROUTE_MESSAGE = "route_message"
    REQUEST_PROTOCOL_RESPONSE = "request_protocol_response"
    RECORD_CHANGES = "record_changes"
    VERIFY = "verify"
    CHALLENGE = "challenge"
    REQUEST_INDEPENDENT_CHECK = "request_independent_check"
    REQUEST_SPECIALIZED_VERIFICATION = "request_specialized_verification"
    ACCEPT_READY = "accept_ready"
    NEEDS_HUMAN = "needs_human"
    STOP_WORKER = "stop_worker"
    WAITING = "waiting"


class AgentMessageType(StrEnum):
    HANDOFF = "handoff"
    QUESTION = "question"
    ANSWER = "answer"
    PLAN_REQUEST = "plan_request"
    PLAN_RESPONSE = "plan_response"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    EVIDENCE = "evidence"
    STATUS = "status"


class ProtocolRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


TERMINAL_WORKER_STATUSES = {WorkerStatus.FAILED, WorkerStatus.STOPPED}
TERMINAL_TASK_STATUSES = {
    CrewTaskStatus.ACCEPTED,
    CrewTaskStatus.REJECTED,
    CrewTaskStatus.BLOCKED,
}
TERMINAL_PROTOCOL_REQUEST_STATUSES = {
    ProtocolRequestStatus.APPROVED,
    ProtocolRequestStatus.REJECTED,
    ProtocolRequestStatus.EXPIRED,
    ProtocolRequestStatus.CANCELLED,
}


def is_terminal_worker_status(status: WorkerStatus | str) -> bool:
    return WorkerStatus(status) in TERMINAL_WORKER_STATUSES


def is_terminal_task_status(status: CrewTaskStatus | str) -> bool:
    return CrewTaskStatus(status) in TERMINAL_TASK_STATUSES


def is_terminal_protocol_request_status(status: ProtocolRequestStatus | str) -> bool:
    return ProtocolRequestStatus(status) in TERMINAL_PROTOCOL_REQUEST_STATUSES


@dataclass(slots=True)
class CrewRecord:
    crew_id: str
    root_goal: str
    repo: str | Path
    status: CrewStatus = CrewStatus.PLANNING
    planner_summary: str = ""
    max_workers: int = 3
    active_worker_ids: list[str] = field(default_factory=list)
    task_graph_path: str | Path = ""
    blackboard_path: str | Path = ""
    verification_summary: str = ""
    merge_summary: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    final_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class WorkerContract:
    contract_id: str
    label: str
    mission: str
    required_capabilities: list[str] = field(default_factory=list)
    authority_level: AuthorityLevel = AuthorityLevel.READONLY
    workspace_policy: WorkspacePolicy = WorkspacePolicy.READONLY
    write_scope: list[str] = field(default_factory=list)
    context_refs: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    protocol_refs: list[str] = field(default_factory=list)
    communication_policy: dict[str, Any] = field(default_factory=dict)
    completion_marker: str = "<<<CODEX_TURN_DONE>>>"
    max_turns: int = 1
    spawn_reason: str = ""
    stop_policy: str = "stop_when_contract_complete"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class AgentProfile:
    profile_id: str
    contract: WorkerContract
    capability_fragments: list[str] = field(default_factory=list)
    protocol_packs: list[str] = field(default_factory=list)
    project_context: str = ""
    completion_marker: str = "<<<CODEX_TURN_DONE>>>"

    def render_prompt(self) -> str:
        capabilities = ", ".join(self.contract.required_capabilities) or "none"
        expected_outputs = "\n".join(f"- {item}" for item in self.contract.expected_outputs) or "- concise status report"
        acceptance = "\n".join(f"- {item}" for item in self.contract.acceptance_criteria) or "- satisfy the mission"
        write_scope = "\n".join(f"- {item}" for item in self.contract.write_scope) or "- no explicit write scope"
        context_refs = "\n".join(f"- {item}" for item in self.contract.context_refs) or "- no external context refs"
        capability_fragments = "\n\n".join(self.capability_fragments)
        protocol_packs = "\n\n".join(self.protocol_packs)
        return (
            f"Capability contract: {self.contract.label}\n"
            f"Mission: {self.contract.mission}\n\n"
            f"Capabilities: {capabilities}\n"
            f"Authority: {self.contract.authority_level.value}\n"
            f"Workspace policy: {self.contract.workspace_policy.value}\n\n"
            f"Write scope:\n{write_scope}\n\n"
            f"Context refs:\n{context_refs}\n\n"
            f"Expected outputs:\n{expected_outputs}\n\n"
            f"Acceptance criteria:\n{acceptance}\n\n"
            f"Capability fragments:\n{capability_fragments or '- no extra capability fragments'}\n\n"
            f"Protocol packs:\n{protocol_packs or '- no extra protocol packs'}\n\n"
            "Communication: emit structured CODEX_MESSAGE blocks for questions, handoffs, evidence, "
            "or protocol requests that need Codex routing.\n"
            f"When this contract turn is complete, print exactly: {self.completion_marker}\n"
            "If Codex sends a per-turn marker later, print the latest per-turn marker instead.\n"
        )

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class CrewEvent:
    event_id: str
    crew_id: str
    worker_id: str | None
    contract_id: str | None
    type: str
    status: str
    artifact_refs: list[str] = field(default_factory=list)
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class WorkerTurnObservation:
    event_id: str
    crew_id: str
    worker_id: str
    contract_id: str | None
    marker: str
    marker_seen: bool
    status: str
    message_blocks: list[dict[str, Any]] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    failure_reason: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class DecisionAction:
    action_id: str
    crew_id: str
    action_type: DecisionActionType
    reason: str
    priority: int = 50
    contract: WorkerContract | None = None
    worker_id: str | None = None
    task_id: str | None = None
    message: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class AgentMessage:
    message_id: str
    thread_id: str
    request_id: str | None
    crew_id: str
    sender: str
    recipient: str
    type: AgentMessageType
    body: str
    artifact_refs: list[str] = field(default_factory=list)
    requires_response: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = _normalize(self)
        data["from"] = data.pop("sender")
        data["to"] = data.pop("recipient")
        return data


@dataclass(slots=True)
class ProtocolRequest:
    request_id: str
    crew_id: str
    type: str
    sender: str
    recipient: str
    status: ProtocolRequestStatus
    subject: str
    body: str = ""
    reason: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = _normalize(self)
        data["from"] = data.pop("sender")
        data["to"] = data.pop("recipient")
        return data


@dataclass(slots=True)
class WorkerRecord:
    worker_id: str
    crew_id: str
    role: WorkerRole
    agent_profile: str
    native_session_id: str
    terminal_session: str
    terminal_pane: str
    transcript_artifact: str
    turn_marker: str
    workspace_mode: WorkspaceMode
    workspace_path: str | Path
    bridge_id: str | None = None
    label: str = ""
    contract_id: str = ""
    capabilities: list[str] = field(default_factory=list)
    authority_level: AuthorityLevel = AuthorityLevel.READONLY
    workspace_allocation_artifact: str = ""
    write_scope: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    status: WorkerStatus = WorkerStatus.CREATED
    assigned_task_ids: list[str] = field(default_factory=list)
    last_seen_at: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class CrewTaskRecord:
    task_id: str
    crew_id: str
    title: str
    instructions: str
    role_required: WorkerRole
    status: CrewTaskStatus = CrewTaskStatus.PENDING
    owner_worker_id: str | None = None
    blocked_by: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    contract_id: str = ""
    required_capabilities: list[str] = field(default_factory=list)
    authority_level: AuthorityLevel = AuthorityLevel.READONLY
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class BlackboardEntry:
    entry_id: str
    crew_id: str
    task_id: str | None
    actor_type: ActorType
    actor_id: str
    type: BlackboardEntryType
    content: str
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)
