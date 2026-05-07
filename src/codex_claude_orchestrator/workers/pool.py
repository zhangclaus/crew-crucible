from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.packs.registry import AgentPackRegistry
from codex_claude_orchestrator.messaging.message_bus import parse_codex_message_blocks
from codex_claude_orchestrator.state.blackboard import BlackboardStore
from codex_claude_orchestrator.crew.models import (
    ActorType,
    AgentProfile,
    AgentMessage,
    AgentMessageType,
    AuthorityLevel,
    BlackboardEntry,
    BlackboardEntryType,
    CrewRecord,
    CrewStatus,
    CrewEvent,
    CrewTaskRecord,
    WorkerContract,
    WorkerRecord,
    WorkerRole,
    WorkerStatus,
    WorkspacePolicy,
    is_terminal_worker_status,
)
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.core.models import WorkspaceAllocation, WorkspaceMode, utc_now
from codex_claude_orchestrator.runtime.native_claude_session import NativeClaudeSession
from codex_claude_orchestrator.messaging.protocol_requests import ProtocolRequestStore
from codex_claude_orchestrator.workspace.worktree_manager import WorktreeManager
from codex_claude_orchestrator.crew.scope import scope_covers_all as _scope_covers_all
from codex_claude_orchestrator.v4.domain_events import DomainEventEmitter


class WorkerPool:
    def __init__(
        self,
        *,
        recorder: CrewRecorder,
        blackboard: BlackboardStore,
        worktree_manager: WorktreeManager,
        native_session: NativeClaudeSession,
        worker_id_factory: Callable[[WorkerRole], str] | None = None,
        entry_id_factory: Callable[[], str] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        message_id_factory: Callable[[], str] | None = None,
        thread_id_factory: Callable[[], str] | None = None,
        agent_pack_registry: AgentPackRegistry | None = None,
        event_store=None,
    ):
        self._recorder = recorder
        self._blackboard = blackboard
        self._worktree_manager = worktree_manager
        self._native_session = native_session
        self._worker_id_factory = worker_id_factory or (lambda role: f"worker-{role.value}-{uuid4().hex[:8]}")
        self._entry_id_factory = entry_id_factory or (lambda: f"entry-{uuid4().hex}")
        self._event_id_factory = event_id_factory or (lambda: f"event-{uuid4().hex}")
        self._message_id_factory = message_id_factory or (lambda: f"msg-{uuid4().hex}")
        self._thread_id_factory = thread_id_factory or (lambda: f"thread-{uuid4().hex}")
        self._agent_pack_registry = agent_pack_registry or AgentPackRegistry.builtin()
        self._domain_events = DomainEventEmitter(event_store) if event_store else None

    def start_worker(
        self,
        *,
        repo_root: Path,
        crew: CrewRecord,
        task: CrewTaskRecord,
        allow_dirty_base: bool = False,
    ) -> WorkerRecord:
        worker_id = self._worker_id_factory(task.role_required)
        allocation = self._allocation_for_task(repo_root, crew.crew_id, worker_id, task, allow_dirty_base)
        allocation_artifact = f"workers/{worker_id}/allocation.json"
        self._recorder.write_text_artifact(
            crew.crew_id,
            allocation_artifact,
            json.dumps(allocation.to_dict(), indent=2, ensure_ascii=False),
        )
        transcript_artifact = f"workers/{worker_id}/transcript.txt"
        transcript_path = self._recorder.write_text_artifact(crew.crew_id, transcript_artifact, "")
        start_info = self._native_session.start(
            repo_root=allocation.path,
            worker_id=worker_id,
            role=task.role_required.value,
            instructions=task.instructions,
            transcript_path=transcript_path,
        )
        worker = WorkerRecord(
            worker_id=worker_id,
            crew_id=crew.crew_id,
            role=task.role_required,
            agent_profile="claude",
            native_session_id=start_info["native_session_id"],
            terminal_session=start_info["terminal_session"],
            terminal_pane=start_info["terminal_pane"],
            transcript_artifact=start_info["transcript_artifact"],
            turn_marker=start_info["turn_marker"],
            workspace_mode=allocation.mode,
            workspace_path=allocation.path,
            workspace_allocation_artifact=allocation_artifact,
            status=WorkerStatus.RUNNING,
            assigned_task_ids=[task.task_id],
        )
        self._recorder.append_worker(crew.crew_id, worker)
        return worker

    def ensure_worker(
        self,
        *,
        repo_root: Path,
        crew: CrewRecord,
        contract: WorkerContract,
        task: CrewTaskRecord | None = None,
        allow_dirty_base: bool = False,
    ) -> WorkerRecord:
        compatible = self.find_compatible_worker(crew.crew_id, contract)
        if compatible is not None:
            return self._worker_from_dict(compatible)

        task = task or self._task_for_contract(crew.crew_id, contract)
        worker_id = self._worker_id_factory(task.role_required)
        allocation = self._allocation_for_contract(repo_root, crew.crew_id, worker_id, contract, allow_dirty_base)
        allocation_artifact = f"workers/{worker_id}/allocation.json"
        self._recorder.write_json_artifact(crew.crew_id, allocation_artifact, allocation.to_dict())
        self._recorder.append_worker_contract(crew.crew_id, contract)
        self._recorder.write_json_artifact(crew.crew_id, f"contracts/{contract.contract_id}.json", contract.to_dict())

        profile = AgentProfile(
            profile_id=f"profile-{worker_id}",
            contract=contract,
            capability_fragments=self._agent_pack_registry.capability_fragments_for(contract.required_capabilities),
            protocol_packs=self._agent_pack_registry.protocol_fragments_for(contract.protocol_refs),
            completion_marker=contract.completion_marker,
        )
        prompt = profile.render_prompt()
        self._recorder.write_text_artifact(crew.crew_id, f"workers/{worker_id}/onboarding_prompt.md", prompt)
        self._recorder.write_text_artifact(
            crew.crew_id,
            f"workers/{worker_id}/agent_profile.md",
            json.dumps(profile.to_dict(), indent=2, ensure_ascii=False),
        )
        transcript_artifact = f"workers/{worker_id}/transcript.txt"
        transcript_path = self._recorder.write_text_artifact(crew.crew_id, transcript_artifact, "")
        start_info = self._native_session.start(
            repo_root=allocation.path,
            worker_id=worker_id,
            role=contract.label,
            instructions=prompt,
            transcript_path=transcript_path,
        )
        worker = WorkerRecord(
            worker_id=worker_id,
            crew_id=crew.crew_id,
            role=task.role_required,
            agent_profile=contract.label,
            native_session_id=start_info["native_session_id"],
            terminal_session=start_info["terminal_session"],
            terminal_pane=start_info["terminal_pane"],
            transcript_artifact=start_info["transcript_artifact"],
            turn_marker=start_info["turn_marker"],
            workspace_mode=allocation.mode,
            workspace_path=allocation.path,
            workspace_allocation_artifact=allocation_artifact,
            label=contract.label,
            contract_id=contract.contract_id,
            capabilities=contract.required_capabilities,
            authority_level=contract.authority_level,
            write_scope=contract.write_scope,
            status=WorkerStatus.RUNNING,
            assigned_task_ids=[task.task_id],
        )
        self._recorder.append_worker(crew.crew_id, worker)
        if self._domain_events:
            self._domain_events.emit_worker_spawned(
                crew.crew_id, worker_id, contract.label, str(allocation.path),
            )
            self._domain_events.emit_worker_contract_recorded(
                crew.crew_id, contract.contract_id, contract.label, contract.mission,
            )
        self._blackboard.append(
            BlackboardEntry(
                entry_id=self._entry_id_factory(),
                crew_id=crew.crew_id,
                task_id=task.task_id,
                actor_type=ActorType.CODEX,
                actor_id="codex",
                type=BlackboardEntryType.DECISION,
                content=f"Spawn worker {worker_id} for contract {contract.contract_id}: {contract.spawn_reason or contract.mission}",
                evidence_refs=[f"contracts/{contract.contract_id}.json", f"workers/{worker_id}/onboarding_prompt.md"],
                confidence=1.0,
            )
        )
        return worker

    def find_compatible_worker(self, crew_id: str, contract: WorkerContract) -> dict | None:
        details = self._recorder.read_crew(crew_id)
        required = set(contract.required_capabilities)
        for worker in details["workers"]:
            if worker.get("status", "running") not in {"running", "idle"}:
                continue
            if not required.issubset(set(worker.get("capabilities", []))):
                continue
            if not self._authority_covers(worker.get("authority_level", AuthorityLevel.READONLY.value), contract.authority_level):
                continue
            if worker.get("workspace_mode") != self._workspace_mode_for_contract(contract).value:
                continue
            if not _scope_covers_all(worker.get("write_scope", []), contract.write_scope):
                continue
            return worker
        return None

    def send_worker(
        self,
        *,
        repo_root: Path,
        crew_id: str,
        worker_id: str,
        message: str,
        turn_marker: str | None = None,
    ) -> dict:
        worker = self._find_worker(crew_id, worker_id)
        self._blackboard.append(
            BlackboardEntry(
                entry_id=self._entry_id_factory(),
                crew_id=crew_id,
                task_id=self._current_task_id(worker),
                actor_type=ActorType.CODEX,
                actor_id="codex",
                type=BlackboardEntryType.DECISION,
                content=message,
                confidence=1.0,
            )
        )
        result = self._native_session.send(terminal_pane=worker["terminal_pane"], message=message, turn_marker=turn_marker)
        self._blackboard.append(
            BlackboardEntry(
                entry_id=self._entry_id_factory(),
                crew_id=crew_id,
                task_id=self._current_task_id(worker),
                actor_type=ActorType.WORKER,
                actor_id=worker_id,
                type=BlackboardEntryType.CLAIM,
                content=json.dumps(result, ensure_ascii=False),
                confidence=0.5,
            )
        )
        return result

    def observe_worker(
        self,
        *,
        repo_root: Path,
        crew_id: str,
        worker_id: str,
        lines: int = 200,
        turn_marker: str | None = None,
    ) -> dict:
        worker = self._find_worker(crew_id, worker_id)
        observation = self._native_session.observe(terminal_pane=worker["terminal_pane"], lines=lines, turn_marker=turn_marker)
        message_blocks = parse_codex_message_blocks(
            observation.get("snapshot", ""),
            crew_id=crew_id,
            sender=worker_id,
            message_id_factory=self._message_id_factory,
            thread_id_factory=self._thread_id_factory,
        )
        for message in message_blocks:
            self._recorder.append_message(crew_id, message)
            self._sync_protocol_request_from_message(message)
        event = CrewEvent(
            event_id=self._event_id_factory(),
            crew_id=crew_id,
            worker_id=worker_id,
            contract_id=worker.get("contract_id") or None,
            type="worker_turn_observed",
            status="completed" if observation.get("marker_seen", False) else "waiting",
            artifact_refs=[worker.get("transcript_artifact", "")] if worker.get("transcript_artifact") else [],
            reason="marker seen" if observation.get("marker_seen", False) else "marker not seen",
            payload={
                "marker": observation.get("marker") or turn_marker,
                "marker_seen": observation.get("marker_seen", False),
                "message_count": len(message_blocks),
            },
        )
        self._recorder.append_event(crew_id, event)
        self._recorder.update_worker(crew_id, worker_id, {"last_seen_at": utc_now()})
        return {
            **observation,
            "message_blocks": [message.to_dict() for message in message_blocks],
            "event": event.to_dict(),
        }

    def attach_worker(self, *, repo_root: Path, crew_id: str, worker_id: str) -> dict:
        worker = self._find_worker(crew_id, worker_id)
        return self._native_session.attach(terminal_session=worker["terminal_session"])

    def tail_worker(self, *, repo_root: Path, crew_id: str, worker_id: str, limit: int = 80) -> dict:
        worker = self._find_worker(crew_id, worker_id)
        return self._native_session.tail(transcript_path=Path(worker["transcript_artifact"]), limit=limit)

    def status_worker(self, *, repo_root: Path, crew_id: str, worker_id: str) -> dict:
        worker = self._find_worker(crew_id, worker_id)
        return self._native_session.status(terminal_session=worker["terminal_session"])

    def stop_worker(self, *, repo_root: Path, crew_id: str, worker_id: str, workspace_cleanup: str = "keep") -> dict:
        worker = self._find_worker(crew_id, worker_id)
        cleanup_result = {"removed": False, "reason": "keep policy"}
        if workspace_cleanup == "remove":
            allocation = self._read_worker_allocation(crew_id, worker)
            cleanup_result = self._worktree_manager.cleanup(repo_root=repo_root, allocation=allocation, remove=True)
        result = self._native_session.stop(terminal_session=worker["terminal_session"])
        self._mark_worker_stopped(crew_id, worker_id)
        if self._domain_events:
            self._domain_events.emit_worker_stopped(crew_id, worker_id)
        return {"worker_id": worker_id, **result, "workspace_cleanup": cleanup_result}

    def stop_crew(self, *, repo_root: Path, crew_id: str, workspace_cleanup: str = "keep") -> dict:
        stopped_workers = []
        for worker in self._recorder.read_crew(crew_id)["workers"]:
            result = self._native_session.stop(terminal_session=worker["terminal_session"])
            cleanup = {"removed": False, "reason": "keep policy"}
            if workspace_cleanup == "remove":
                try:
                    allocation = self._read_worker_allocation(crew_id, worker)
                    cleanup = self._worktree_manager.cleanup(repo_root=repo_root, allocation=allocation, remove=True)
                except Exception as exc:
                    cleanup = {"removed": False, "reason": str(exc)}
            self._mark_worker_stopped(crew_id, worker["worker_id"])
            stopped_workers.append({"worker_id": worker["worker_id"], **result, "workspace_cleanup": cleanup})
        return {"crew_id": crew_id, "stopped_workers": stopped_workers}

    def claim_worker(self, crew_id: str, worker_id: str) -> None:
        """Transition worker from RUNNING/IDLE to BUSY."""
        worker = self._find_worker(crew_id, worker_id)
        current = worker.get("status", "running")
        if current not in {"running", "idle"}:
            raise ValueError(f"Cannot claim worker {worker_id} in status {current}")
        transitioned = self._recorder.transition_worker_status(
            crew_id, worker_id, expected_status=current, new_status="busy",
        )
        if not transitioned:
            raise ValueError(f"Claim race: worker {worker_id} changed status concurrently")
        self._recorder.append_event(crew_id, CrewEvent(
            event_id=self._event_id_factory(),
            crew_id=crew_id,
            worker_id=worker_id,
            contract_id=None,
            type="worker_claimed",
            status="completed",
        ))
        if self._domain_events:
            self._domain_events.emit_worker_claimed(crew_id, worker_id)

    def release_worker(self, crew_id: str, worker_id: str) -> None:
        """Transition worker from BUSY to IDLE (idempotent)."""
        transitioned = self._recorder.transition_worker_status(
            crew_id, worker_id, expected_status="busy", new_status="idle",
        )
        if not transitioned:
            return  # idempotent
        self._recorder.append_event(crew_id, CrewEvent(
            event_id=self._event_id_factory(),
            crew_id=crew_id,
            worker_id=worker_id,
            contract_id=None,
            type="worker_released",
            status="completed",
        ))
        if self._domain_events:
            self._domain_events.emit_worker_released(crew_id, worker_id)

    def prune_orphans(self, *, repo_root: Path) -> dict:
        result = self._native_session.prune_orphans(active_sessions=self._active_terminal_sessions())
        # Recover stale BUSY workers from crashed claim/release pairs.
        recovered: list[str] = []
        for crew in self._recorder.list_crews():
            if crew["status"] == CrewStatus.RUNNING.value:
                recovered.extend(self._recorder.recover_stale_busy_workers(crew["crew_id"]))
        if recovered:
            result["recovered_workers"] = recovered
        return result

    def _allocation_for_task(
        self,
        repo_root: Path,
        crew_id: str,
        worker_id: str,
        task: CrewTaskRecord,
        allow_dirty_base: bool,
    ) -> WorkspaceAllocation:
        if task.role_required is WorkerRole.IMPLEMENTER:
            return self._worktree_manager.prepare(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=worker_id,
                allow_dirty_base=allow_dirty_base,
            )
        return WorkspaceAllocation(
            workspace_id=f"{crew_id}-{worker_id}",
            path=repo_root.resolve(),
            mode=WorkspaceMode.READONLY,
            writable=False,
        )

    def _allocation_for_contract(
        self,
        repo_root: Path,
        crew_id: str,
        worker_id: str,
        contract: WorkerContract,
        allow_dirty_base: bool,
    ) -> WorkspaceAllocation:
        mode = self._workspace_mode_for_contract(contract)
        if mode is WorkspaceMode.WORKTREE:
            return self._worktree_manager.prepare(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=worker_id,
                allow_dirty_base=allow_dirty_base,
            )
        return WorkspaceAllocation(
            workspace_id=f"{crew_id}-{worker_id}",
            path=repo_root.resolve(),
            mode=mode,
            writable=mode is WorkspaceMode.SHARED,
        )

    def _workspace_mode_for_contract(self, contract: WorkerContract) -> WorkspaceMode:
        if contract.workspace_policy is WorkspacePolicy.WORKTREE:
            return WorkspaceMode.WORKTREE
        if contract.workspace_policy is WorkspacePolicy.SHARED:
            return WorkspaceMode.SHARED
        if contract.authority_level in {AuthorityLevel.SOURCE_WRITE, AuthorityLevel.TEST_WRITE, AuthorityLevel.STATE_WRITE}:
            return WorkspaceMode.WORKTREE
        return WorkspaceMode.READONLY

    def _find_worker(self, crew_id: str, worker_id: str) -> dict:
        for worker in self._recorder.read_crew(crew_id)["workers"]:
            if worker["worker_id"] == worker_id:
                return worker
        raise FileNotFoundError(f"worker not found: {worker_id}")

    def _mark_worker_stopped(self, crew_id: str, worker_id: str) -> None:
        self._recorder.update_worker(crew_id, worker_id, {"status": WorkerStatus.STOPPED.value})

    def _active_terminal_sessions(self) -> set[str]:
        active = set()
        for crew in self._recorder.list_crews():
            if crew["status"] != CrewStatus.RUNNING.value:
                continue
            details = self._recorder.read_crew(crew["crew_id"])
            active_worker_ids = set(self._recorder.active_worker_ids(crew["crew_id"]))
            active.update(
                worker["terminal_session"]
                for worker in details["workers"]
                if worker.get("terminal_session") and worker["worker_id"] in active_worker_ids
            )
        return active

    def _current_task_id(self, worker: dict) -> str | None:
        task_ids = worker.get("assigned_task_ids") or []
        return task_ids[0] if task_ids else None

    def _read_worker_allocation(self, crew_id: str, worker: dict) -> WorkspaceAllocation:
        artifact = worker.get("workspace_allocation_artifact")
        if not artifact:
            return WorkspaceAllocation(
                workspace_id=f"{crew_id}-{worker['worker_id']}",
                path=Path(worker["workspace_path"]),
                mode=WorkspaceMode(worker["workspace_mode"]),
                writable=worker.get("workspace_mode") != WorkspaceMode.READONLY.value,
            )
        payload = json.loads((self._recorder._crew_dir(crew_id) / "artifacts" / artifact).read_text(encoding="utf-8"))
        return WorkspaceAllocation(
            workspace_id=payload["workspace_id"],
            path=Path(payload["path"]),
            mode=WorkspaceMode(payload["mode"]),
            writable=payload["writable"],
            baseline_snapshot=payload.get("baseline_snapshot", {}),
            branch=payload.get("branch", ""),
            base_ref=payload.get("base_ref", ""),
            base_patch_artifact=payload.get("base_patch_artifact", ""),
        )

    def _sync_protocol_request_from_message(self, message: AgentMessage) -> None:
        store = ProtocolRequestStore(self._recorder)
        if message.type in {
            AgentMessageType.PLAN_REQUEST,
            AgentMessageType.SHUTDOWN_REQUEST,
        }:
            if not message.request_id:
                return
            if store.latest(message.crew_id, message.request_id) is None:
                store.create(
                    crew_id=message.crew_id,
                    request_id=message.request_id,
                    request_type=message.type.value,
                    sender=message.sender,
                    recipient=message.recipient,
                    subject=message.body.splitlines()[0] if message.body else message.type.value,
                    body=message.body,
                    artifact_refs=message.artifact_refs,
                )
            return

        if message.type in {
            AgentMessageType.PLAN_RESPONSE,
            AgentMessageType.SHUTDOWN_RESPONSE,
        }:
            if not message.request_id:
                return
            response_status = message.metadata.get("response_status", "").strip().lower()
            if response_status not in {"approved", "rejected", "expired", "cancelled"}:
                return
            if store.latest(message.crew_id, message.request_id) is not None:
                store.transition(
                    crew_id=message.crew_id,
                    request_id=message.request_id,
                    status=response_status,
                    reason=message.body,
                )

    def _task_for_contract(self, crew_id: str, contract: WorkerContract) -> CrewTaskRecord:
        role = WorkerRole.IMPLEMENTER
        if contract.authority_level is AuthorityLevel.READONLY:
            role = WorkerRole.REVIEWER if "review_patch" in contract.required_capabilities else WorkerRole.EXPLORER
        return CrewTaskRecord(
            task_id=f"task-{contract.contract_id}",
            crew_id=crew_id,
            title=contract.label,
            instructions=contract.mission,
            role_required=role,
            expected_outputs=contract.expected_outputs,
            acceptance_criteria=contract.acceptance_criteria,
            contract_id=contract.contract_id,
            required_capabilities=contract.required_capabilities,
            authority_level=contract.authority_level,
        )

    def _worker_from_dict(self, payload: dict) -> WorkerRecord:
        return WorkerRecord(
            worker_id=payload["worker_id"],
            crew_id=payload["crew_id"],
            role=WorkerRole(payload["role"]),
            agent_profile=payload["agent_profile"],
            native_session_id=payload["native_session_id"],
            terminal_session=payload["terminal_session"],
            terminal_pane=payload["terminal_pane"],
            transcript_artifact=payload["transcript_artifact"],
            turn_marker=payload["turn_marker"],
            workspace_mode=WorkspaceMode(payload["workspace_mode"]),
            workspace_path=Path(payload["workspace_path"]),
            bridge_id=payload.get("bridge_id"),
            label=payload.get("label", ""),
            contract_id=payload.get("contract_id", ""),
            capabilities=payload.get("capabilities", []),
            authority_level=AuthorityLevel(payload.get("authority_level", AuthorityLevel.READONLY.value)),
            workspace_allocation_artifact=payload.get("workspace_allocation_artifact", ""),
            write_scope=payload.get("write_scope", []),
            allowed_tools=payload.get("allowed_tools", []),
            status=WorkerStatus(payload.get("status", WorkerStatus.CREATED.value)),
            assigned_task_ids=payload.get("assigned_task_ids", []),
            last_seen_at=payload.get("last_seen_at"),
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )

    def _authority_covers(self, worker_authority: str, required_authority: AuthorityLevel) -> bool:
        ranks = {
            AuthorityLevel.READONLY: 0,
            AuthorityLevel.TEST_WRITE: 1,
            AuthorityLevel.SOURCE_WRITE: 2,
            AuthorityLevel.STATE_WRITE: 3,
        }
        return ranks[AuthorityLevel(worker_authority)] >= ranks[required_authority]
