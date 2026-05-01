from pathlib import Path

from codex_claude_orchestrator.crew.models import (
    ActorType,
    AgentMessage,
    AgentMessageType,
    AuthorityLevel,
    BlackboardEntry,
    BlackboardEntryType,
    CrewRecord,
    CrewEvent,
    CrewStatus,
    CrewTaskRecord,
    CrewTaskStatus,
    DecisionAction,
    DecisionActionType,
    ProtocolRequest,
    ProtocolRequestStatus,
    WorkerContract,
    WorkerRecord,
    WorkerRole,
    WorkerStatus,
    WorkspacePolicy,
)
from codex_claude_orchestrator.core.models import WorkspaceMode


def test_crew_record_serializes_enums_paths_and_worker_ids():
    crew = CrewRecord(
        crew_id="crew-1",
        root_goal="Build V3 MVP",
        repo=Path("/repo"),
        status=CrewStatus.RUNNING,
        active_worker_ids=["worker-explorer"],
    )

    data = crew.to_dict()

    assert data["crew_id"] == "crew-1"
    assert data["repo"] == "/repo"
    assert data["status"] == "running"
    assert data["active_worker_ids"] == ["worker-explorer"]


def test_worker_task_blackboard_serialization_matches_mvp_schema():
    worker = WorkerRecord(
        worker_id="worker-implementer",
        crew_id="crew-1",
        role=WorkerRole.IMPLEMENTER,
        agent_profile="claude",
        native_session_id="native-1",
        terminal_session="crew-1-worker-implementer",
        terminal_pane="crew-1-worker-implementer:claude.0",
        transcript_artifact="workers/worker-implementer/transcript.txt",
        turn_marker="<<<CODEX_TURN_DONE>>>",
        bridge_id=None,
        workspace_mode=WorkspaceMode.WORKTREE,
        workspace_path=Path("/tmp/worktree"),
        workspace_allocation_artifact="workers/worker-implementer/allocation.json",
        status=WorkerStatus.RUNNING,
        assigned_task_ids=["task-implementer"],
    )
    task = CrewTaskRecord(
        task_id="task-implementer",
        crew_id="crew-1",
        title="Implement patch",
        instructions="Modify the worker worktree branch.",
        role_required=WorkerRole.IMPLEMENTER,
        status=CrewTaskStatus.ASSIGNED,
        owner_worker_id=worker.worker_id,
    )
    entry = BlackboardEntry(
        entry_id="entry-1",
        crew_id="crew-1",
        task_id=task.task_id,
        actor_type=ActorType.WORKER,
        actor_id=worker.worker_id,
        type=BlackboardEntryType.PATCH,
        content="Changed app.py in worker worktree.",
        evidence_refs=["app.py"],
        confidence=0.8,
    )

    assert worker.to_dict()["role"] == "implementer"
    assert worker.to_dict()["workspace_mode"] == "worktree"
    assert task.to_dict()["status"] == "assigned"
    assert entry.to_dict()["actor_type"] == "worker"
    assert entry.to_dict()["type"] == "patch"


def test_dynamic_contract_event_message_and_protocol_serialization_matches_v3_schema():
    contract = WorkerContract(
        contract_id="contract-source-write",
        label="targeted-code-editor",
        mission="Implement the smallest safe patch.",
        required_capabilities=["inspect_code", "edit_source", "edit_tests"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
        write_scope=["src/", "tests/"],
        expected_outputs=["patch", "changed_files", "verification_notes"],
        acceptance_criteria=["pytest passes"],
        completion_marker="<<<CODEX_TURN_DONE>>>",
        spawn_reason="goal requires source edits",
    )
    event = CrewEvent(
        event_id="event-1",
        crew_id="crew-1",
        worker_id="worker-source",
        contract_id=contract.contract_id,
        type="worker_turn_observed",
        status="completed",
        artifact_refs=["workers/worker-source/transcript.txt"],
        reason="marker seen",
    )
    action = DecisionAction(
        action_id="decision-1",
        crew_id="crew-1",
        action_type=DecisionActionType.SPAWN_WORKER,
        contract=contract,
        reason="no compatible source_write worker is active",
        priority=90,
    )
    message = AgentMessage(
        message_id="msg-1",
        thread_id="thread-1",
        request_id="req-1",
        crew_id="crew-1",
        sender="worker-source",
        recipient="codex",
        type=AgentMessageType.QUESTION,
        body="Need an auditor before editing API contracts.",
        requires_response=True,
    )
    request = ProtocolRequest(
        request_id="req-1",
        crew_id="crew-1",
        type="plan_request",
        sender="worker-source",
        recipient="codex",
        status=ProtocolRequestStatus.PENDING,
        subject="Edit verification pipeline",
    )

    worker = WorkerRecord(
        worker_id="worker-source",
        crew_id="crew-1",
        role=WorkerRole.IMPLEMENTER,
        agent_profile="targeted-code-editor",
        native_session_id="native-1",
        terminal_session="crew-worker-source",
        terminal_pane="crew-worker-source:claude.0",
        transcript_artifact="workers/worker-source/transcript.txt",
        turn_marker="<<<CODEX_TURN_DONE>>>",
        workspace_mode=WorkspaceMode.WORKTREE,
        workspace_path=Path("/tmp/worktree"),
        label=contract.label,
        contract_id=contract.contract_id,
        capabilities=contract.required_capabilities,
        authority_level=AuthorityLevel.SOURCE_WRITE,
    )

    assert contract.to_dict()["authority_level"] == "source_write"
    assert contract.to_dict()["workspace_policy"] == "worktree"
    assert event.to_dict()["artifact_refs"] == ["workers/worker-source/transcript.txt"]
    assert action.to_dict()["contract"]["label"] == "targeted-code-editor"
    assert action.to_dict()["action_type"] == "spawn_worker"
    assert message.to_dict()["from"] == "worker-source"
    assert message.to_dict()["to"] == "codex"
    assert message.to_dict()["type"] == "question"
    assert request.to_dict()["status"] == "pending"
    assert worker.to_dict()["label"] == "targeted-code-editor"
    assert worker.to_dict()["contract_id"] == "contract-source-write"
    assert worker.to_dict()["capabilities"] == ["inspect_code", "edit_source", "edit_tests"]
    assert worker.to_dict()["authority_level"] == "source_write"
