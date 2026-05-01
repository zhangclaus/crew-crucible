from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.state.blackboard import BlackboardStore
from codex_claude_orchestrator.crew.models import (
    ActorType,
    AuthorityLevel,
    BlackboardEntry,
    BlackboardEntryType,
    CrewRecord,
    CrewStatus,
    CrewTaskRecord,
    CrewTaskStatus,
    WorkerContract,
    WorkerRole,
    WorkspacePolicy,
)
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.core.models import WorkspaceAllocation, WorkspaceMode
from codex_claude_orchestrator.crew.task_graph import TaskGraphPlanner


class CrewController:
    def __init__(
        self,
        *,
        recorder: CrewRecorder,
        blackboard: BlackboardStore,
        task_graph: TaskGraphPlanner,
        worker_pool,
        verification_runner=None,
        change_recorder=None,
        merge_arbiter=None,
        crew_id_factory=None,
        entry_id_factory=None,
    ):
        self._recorder = recorder
        self._blackboard = blackboard
        self._task_graph = task_graph
        self._worker_pool = worker_pool
        self._verification_runner = verification_runner
        self._change_recorder = change_recorder
        self._merge_arbiter = merge_arbiter
        self._crew_id_factory = crew_id_factory or (lambda: f"crew-{uuid4().hex}")
        self._entry_id_factory = entry_id_factory or (lambda: f"entry-{uuid4().hex}")

    def start(
        self,
        *,
        repo_root: Path,
        goal: str,
        worker_roles: list[WorkerRole],
        allow_dirty_base: bool = False,
    ) -> CrewRecord:
        crew = CrewRecord(
            crew_id=self._crew_id_factory(),
            root_goal=goal,
            repo=repo_root,
            task_graph_path="tasks.json",
            blackboard_path="blackboard.jsonl",
        )
        self._recorder.start_crew(crew)
        self._blackboard.append(
            BlackboardEntry(
                entry_id=self._entry_id_factory(),
                crew_id=crew.crew_id,
                task_id=None,
                actor_type=ActorType.CODEX,
                actor_id="codex",
                type=BlackboardEntryType.DECISION,
                content=f"Start crew for goal: {goal}",
                confidence=1.0,
            )
        )
        role_set = set(worker_roles)
        tasks = [task for task in self._task_graph.default_graph(crew.crew_id, goal) if task.role_required in role_set]
        active_worker_ids: list[str] = []
        try:
            for task in tasks:
                worker = self._worker_pool.start_worker(
                    repo_root=repo_root,
                    crew=crew,
                    task=task,
                    allow_dirty_base=allow_dirty_base,
                )
                active_worker_ids.append(worker.worker_id)
                self._task_graph.assign(tasks, task.task_id, worker.worker_id)
        except Exception as exc:
            self._worker_pool.stop_crew(repo_root=repo_root, crew_id=crew.crew_id)
            self._recorder.update_crew(crew.crew_id, {"active_worker_ids": []})
            self._recorder.finalize_crew(crew.crew_id, CrewStatus.FAILED, f"crew start failed: {exc}")
            raise

        self._recorder.write_tasks(crew.crew_id, tasks)
        crew.status = CrewStatus.RUNNING
        crew.active_worker_ids = active_worker_ids
        self._recorder.update_crew(
            crew.crew_id,
            {"status": CrewStatus.RUNNING.value, "active_worker_ids": active_worker_ids},
        )
        return crew

    def start_dynamic(
        self,
        *,
        repo_root: Path,
        goal: str,
    ) -> CrewRecord:
        crew = CrewRecord(
            crew_id=self._crew_id_factory(),
            root_goal=goal,
            repo=repo_root,
            task_graph_path="tasks.json",
            blackboard_path="blackboard.jsonl",
            active_worker_ids=[],
        )
        self._recorder.start_crew(crew)
        self._blackboard.append(
            BlackboardEntry(
                entry_id=self._entry_id_factory(),
                crew_id=crew.crew_id,
                task_id=None,
                actor_type=ActorType.CODEX,
                actor_id="codex",
                type=BlackboardEntryType.DECISION,
                content=f"Start dynamic crew for goal: {goal}",
                confidence=1.0,
            )
        )
        self._recorder.write_tasks(crew.crew_id, [])
        crew.status = CrewStatus.RUNNING
        self._recorder.update_crew(
            crew.crew_id,
            {"status": CrewStatus.RUNNING.value, "active_worker_ids": []},
        )
        self.write_team_snapshot(crew_id=crew.crew_id, last_decision={"action_type": "start_dynamic"})
        return crew

    def ensure_worker(
        self,
        *,
        repo_root: Path,
        crew_id: str,
        contract: WorkerContract,
        allow_dirty_base: bool = False,
    ) -> dict:
        details = self._recorder.read_crew(crew_id)
        crew = self._crew_from_dict(details["crew"])
        task = self._task_graph.task_for_contract(crew_id, contract)
        worker = self._worker_pool.ensure_worker(
            repo_root=repo_root,
            crew=crew,
            contract=contract,
            task=task,
            allow_dirty_base=allow_dirty_base,
        )
        worker_payload = worker.to_dict() if hasattr(worker, "to_dict") else dict(worker)
        tasks = [self._task_from_dict(item) for item in details.get("tasks", [])]
        task.owner_worker_id = worker_payload["worker_id"]
        task.status = CrewTaskStatus.ASSIGNED
        tasks = [existing for existing in tasks if existing.task_id != task.task_id]
        tasks.append(task)
        self._recorder.write_tasks(crew_id, tasks)
        active_worker_ids = list(self._recorder.read_crew(crew_id)["crew"].get("active_worker_ids") or [])
        if worker_payload["worker_id"] not in active_worker_ids:
            active_worker_ids.append(worker_payload["worker_id"])
            self._recorder.update_crew(crew_id, {"active_worker_ids": active_worker_ids})
        self.write_team_snapshot(
            crew_id=crew_id,
            last_decision={"action_type": "spawn_worker", "contract_id": contract.contract_id},
        )
        return worker_payload

    def write_team_snapshot(self, *, crew_id: str, last_decision: dict | None = None) -> dict:
        details = self._recorder.read_crew(crew_id)
        contracts = details.get("worker_contracts") or [
            {
                "contract_id": task.get("contract_id", ""),
                "label": task.get("title", ""),
                "required_capabilities": task.get("required_capabilities", []),
                "authority_level": task.get("authority_level", AuthorityLevel.READONLY.value),
            }
            for task in details.get("tasks", [])
            if task.get("contract_id")
        ]
        workers = details.get("workers") or [
            {"worker_id": worker_id}
            for worker_id in details["crew"].get("active_worker_ids", [])
        ]
        payload = {
            "crew_id": crew_id,
            "capability_registry_version": "builtin-mvp-v1",
            "decision_policy_version": "dynamic-contract-mvp-v1",
            "capabilities_available": sorted(
                {
                    capability
                    for contract in contracts
                    for capability in contract.get("required_capabilities", [])
                }
            ),
            "contracts_created": contracts,
            "workers_spawned": workers,
            "message_cursor_summary": details.get("message_cursors", {}),
            "open_protocol_requests": [
                request
                for request in details.get("protocol_requests", [])
                if request.get("status") == "pending"
            ],
            "prompt_artifacts": {
                artifact: artifact
                for artifact in details.get("artifacts", [])
                if artifact.endswith(("onboarding_prompt.md", "agent_profile.md"))
            },
            "last_decision": last_decision or {},
            "resume_hint": "Read team_snapshot.json and blackboard before supervising.",
        }
        self._recorder.write_team_snapshot(crew_id, payload)
        return payload

    def append_known_pitfall(
        self,
        *,
        crew_id: str,
        failure_class: str,
        summary: str,
        guardrail: str,
        evidence_refs: list[str] | None = None,
    ) -> dict:
        return self._recorder.append_known_pitfall(
            crew_id,
            failure_class=failure_class,
            summary=summary,
            guardrail=guardrail,
            evidence_refs=evidence_refs or [],
        )

    def write_json_artifact(self, *, crew_id: str, artifact_name: str, payload) -> str:
        self._recorder.write_json_artifact(crew_id, artifact_name, payload)
        return artifact_name

    def record_blackboard_entry(
        self,
        *,
        crew_id: str,
        entry_type: BlackboardEntryType | str,
        content: str,
        evidence_refs: list[str] | None = None,
        task_id: str | None = None,
        actor_type: ActorType | str = ActorType.CODEX,
        actor_id: str = "codex",
        confidence: float = 1.0,
    ) -> dict:
        entry = BlackboardEntry(
            entry_id=self._entry_id_factory(),
            crew_id=crew_id,
            task_id=task_id,
            actor_type=ActorType(actor_type),
            actor_id=actor_id,
            type=BlackboardEntryType(entry_type),
            content=content,
            evidence_refs=evidence_refs or [],
            confidence=confidence,
        )
        self._blackboard.append(entry)
        return entry.to_dict()

    def record_decision(self, *, crew_id: str, action) -> dict:
        payload = action.to_dict() if hasattr(action, "to_dict") else dict(action)
        if hasattr(action, "to_dict"):
            self._recorder.append_decision(crew_id, action)
        else:
            from codex_claude_orchestrator.crew.models import DecisionAction, DecisionActionType, WorkerContract

            contract_payload = payload.get("contract")
            contract = None
            if contract_payload:
                contract = WorkerContract(
                    contract_id=contract_payload["contract_id"],
                    label=contract_payload["label"],
                    mission=contract_payload["mission"],
                    required_capabilities=contract_payload.get("required_capabilities", []),
                    authority_level=AuthorityLevel(contract_payload.get("authority_level", AuthorityLevel.READONLY.value)),
                    workspace_policy=WorkspacePolicy(contract_payload.get("workspace_policy", WorkspacePolicy.READONLY.value)),
                    write_scope=contract_payload.get("write_scope", []),
                    context_refs=contract_payload.get("context_refs", []),
                    expected_outputs=contract_payload.get("expected_outputs", []),
                    acceptance_criteria=contract_payload.get("acceptance_criteria", []),
                    protocol_refs=contract_payload.get("protocol_refs", []),
                    communication_policy=contract_payload.get("communication_policy", {}),
                    completion_marker=contract_payload.get("completion_marker", "<<<CODEX_TURN_DONE>>>"),
                    max_turns=contract_payload.get("max_turns", 1),
                    spawn_reason=contract_payload.get("spawn_reason", ""),
                    stop_policy=contract_payload.get("stop_policy", "stop_when_contract_complete"),
                    created_at=contract_payload.get("created_at"),
                )
            decision = DecisionAction(
                action_id=payload["action_id"],
                crew_id=payload["crew_id"],
                action_type=DecisionActionType(payload["action_type"]),
                reason=payload["reason"],
                priority=payload.get("priority", 50),
                contract=contract,
                worker_id=payload.get("worker_id"),
                task_id=payload.get("task_id"),
                message=payload.get("message", ""),
                created_at=payload.get("created_at"),
            )
            self._recorder.append_decision(crew_id, decision)
        return payload

    def resume_context(self, *, crew_id: str) -> dict:
        details = self._recorder.read_crew(crew_id)
        return {
            "crew": details["crew"],
            "team_snapshot": details.get("team_snapshot"),
            "blackboard": details.get("blackboard", []),
            "decisions": details.get("decisions", []),
            "messages": details.get("messages", []),
            "message_cursors": details.get("message_cursors", {}),
            "open_protocol_requests": [
                request
                for request in details.get("protocol_requests", [])
                if request.get("status") == "pending"
            ],
            "protocol_requests": details.get("protocol_requests", []),
            "known_pitfalls": details.get("known_pitfalls", []),
            "workers": details.get("workers", []),
            "contracts": details.get("worker_contracts", []),
            "resume_hint": "Replay decisions, protocol requests, and blackboard before sending the next worker turn.",
        }

    def status(self, *, repo_root: Path, crew_id: str) -> dict:
        return self._recorder.read_crew(crew_id)

    def blackboard_entries(self, *, crew_id: str) -> list[dict]:
        return self._blackboard.list_entries(crew_id)

    def send_worker(
        self,
        *,
        repo_root: Path,
        crew_id: str,
        worker_id: str,
        message: str,
        turn_marker: str | None = None,
    ) -> dict:
        return self._worker_pool.send_worker(
            repo_root=repo_root,
            crew_id=crew_id,
            worker_id=worker_id,
            message=message,
            turn_marker=turn_marker,
        )

    def observe_worker(
        self,
        *,
        repo_root: Path,
        crew_id: str,
        worker_id: str,
        lines: int = 200,
        turn_marker: str | None = None,
    ) -> dict:
        return self._worker_pool.observe_worker(
            repo_root=repo_root,
            crew_id=crew_id,
            worker_id=worker_id,
            lines=lines,
            turn_marker=turn_marker,
        )

    def attach_worker(self, *, repo_root: Path, crew_id: str, worker_id: str) -> dict:
        return self._worker_pool.attach_worker(repo_root=repo_root, crew_id=crew_id, worker_id=worker_id)

    def tail_worker(self, *, repo_root: Path, crew_id: str, worker_id: str, limit: int = 80) -> dict:
        return self._worker_pool.tail_worker(repo_root=repo_root, crew_id=crew_id, worker_id=worker_id, limit=limit)

    def status_worker(self, *, repo_root: Path, crew_id: str, worker_id: str) -> dict:
        return self._worker_pool.status_worker(repo_root=repo_root, crew_id=crew_id, worker_id=worker_id)

    def stop_worker(self, *, repo_root: Path, crew_id: str, worker_id: str, workspace_cleanup: str = "keep") -> dict:
        return self._worker_pool.stop_worker(
            repo_root=repo_root,
            crew_id=crew_id,
            worker_id=worker_id,
            workspace_cleanup=workspace_cleanup,
        )

    def stop(self, *, repo_root: Path, crew_id: str) -> dict:
        stop_result = self._worker_pool.stop_crew(repo_root=repo_root, crew_id=crew_id)
        self._recorder.update_crew(crew_id, {"active_worker_ids": []})
        self._recorder.finalize_crew(crew_id, CrewStatus.CANCELLED, "crew stopped by Codex")
        return {"crew_id": crew_id, "status": CrewStatus.CANCELLED.value, "stop": stop_result}

    def prune_orphans(self, *, repo_root: Path) -> dict:
        return self._worker_pool.prune_orphans(repo_root=repo_root)

    def verify(self, *, crew_id: str, command: str, worker_id: str | None = None) -> dict:
        if self._verification_runner is None:
            raise ValueError("crew verification runner is not configured")
        target_worker_id, cwd = self._verification_target(crew_id, worker_id)
        return self._verification_runner.run(
            crew_id=crew_id,
            command=command,
            cwd=cwd,
            target_worker_id=target_worker_id,
        )

    def challenge(self, *, crew_id: str, summary: str, task_id: str | None = None) -> dict:
        entry = BlackboardEntry(
            entry_id=self._entry_id_factory(),
            crew_id=crew_id,
            task_id=task_id,
            actor_type=ActorType.CODEX,
            actor_id="codex",
            type=BlackboardEntryType.RISK,
            content=summary,
            confidence=1.0,
        )
        self._blackboard.append(entry)
        self._mark_task_status(crew_id, task_id, CrewTaskStatus.CHALLENGED)
        return entry.to_dict()

    def accept(self, *, crew_id: str, summary: str) -> dict:
        self._recorder.finalize_crew(crew_id, CrewStatus.ACCEPTED, summary)
        stop_result = self._worker_pool.stop_crew(repo_root=Path(self._recorder.read_crew(crew_id)["crew"]["repo"]), crew_id=crew_id)
        return {"crew_id": crew_id, "status": CrewStatus.ACCEPTED.value, "summary": summary, "stop": stop_result}

    def changes(self, *, crew_id: str, worker_id: str) -> dict:
        if self._change_recorder is None:
            raise ValueError("crew change recorder is not configured")
        allocation = self._read_worker_allocation(crew_id, worker_id)
        return self._change_recorder.record_changes(crew_id, worker_id, allocation)

    def merge_plan(self, *, crew_id: str) -> dict:
        if self._merge_arbiter is None:
            raise ValueError("crew merge arbiter is not configured")
        details = self._recorder.read_crew(crew_id)
        changed_files_by_worker: dict[str, list[str]] = {}
        for artifact in details["artifacts"]:
            if artifact.endswith("/changes.json"):
                payload = json.loads((self._crew_artifact_root(crew_id) / artifact).read_text(encoding="utf-8"))
                changed_files_by_worker[payload["worker_id"]] = payload["changed_files"]
        plan = self._merge_arbiter.build_plan(crew_id, changed_files_by_worker=changed_files_by_worker)
        self._recorder.write_text_artifact(crew_id, "merge_plan.json", json.dumps(plan, indent=2, ensure_ascii=False))
        self._recorder.update_crew(crew_id, {"merge_summary": plan["recommendation"]})
        return plan

    def _mark_task_status(self, crew_id: str, task_id: str | None, status: CrewTaskStatus) -> None:
        if task_id is None:
            return
        details = self._recorder.read_crew(crew_id)
        tasks = []
        for item in details["tasks"]:
            task = self._task_from_dict(item)
            if task.task_id == task_id:
                task.status = status
            tasks.append(task)
        self._recorder.write_tasks(crew_id, tasks)

    def _read_worker_allocation(self, crew_id: str, worker_id: str) -> WorkspaceAllocation:
        details = self._recorder.read_crew(crew_id)
        worker = next((item for item in details["workers"] if item["worker_id"] == worker_id), None)
        if worker is None:
            raise FileNotFoundError(f"worker not found: {worker_id}")
        artifact = worker["workspace_allocation_artifact"]
        payload = json.loads((self._crew_artifact_root(crew_id) / artifact).read_text(encoding="utf-8"))
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

    def _verification_target(self, crew_id: str, worker_id: str | None) -> tuple[str | None, Path]:
        details = self._recorder.read_crew(crew_id)
        worker = None
        if worker_id is not None:
            worker = next((item for item in details["workers"] if item["worker_id"] == worker_id), None)
            if worker is None:
                raise FileNotFoundError(f"worker not found: {worker_id}")
        else:
            worker = next((item for item in details["workers"] if item.get("role") == WorkerRole.IMPLEMENTER.value), None)
        if worker is None:
            return None, Path(details["crew"]["repo"])
        return worker["worker_id"], Path(worker["workspace_path"])

    def _crew_artifact_root(self, crew_id: str) -> Path:
        return self._recorder._crew_dir(crew_id) / "artifacts"

    def _task_from_dict(self, payload: dict) -> CrewTaskRecord:
        return CrewTaskRecord(
            task_id=payload["task_id"],
            crew_id=payload["crew_id"],
            title=payload["title"],
            instructions=payload["instructions"],
            role_required=WorkerRole(payload["role_required"]),
            status=CrewTaskStatus(payload["status"]),
            owner_worker_id=payload.get("owner_worker_id"),
            blocked_by=payload.get("blocked_by", []),
            depends_on=payload.get("depends_on", []),
            allowed_paths=payload.get("allowed_paths", []),
            forbidden_paths=payload.get("forbidden_paths", []),
            expected_outputs=payload.get("expected_outputs", []),
            acceptance_criteria=payload.get("acceptance_criteria", []),
            evidence_refs=payload.get("evidence_refs", []),
            contract_id=payload.get("contract_id", ""),
            required_capabilities=payload.get("required_capabilities", []),
            authority_level=AuthorityLevel(payload.get("authority_level", AuthorityLevel.READONLY.value)),
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )

    def _crew_from_dict(self, payload: dict) -> CrewRecord:
        return CrewRecord(
            crew_id=payload["crew_id"],
            root_goal=payload["root_goal"],
            repo=payload["repo"],
            status=CrewStatus(payload.get("status", CrewStatus.PLANNING.value)),
            planner_summary=payload.get("planner_summary", ""),
            max_workers=payload.get("max_workers", 3),
            active_worker_ids=payload.get("active_worker_ids", []),
            task_graph_path=payload.get("task_graph_path", ""),
            blackboard_path=payload.get("blackboard_path", ""),
            verification_summary=payload.get("verification_summary", ""),
            merge_summary=payload.get("merge_summary", ""),
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            ended_at=payload.get("ended_at"),
            final_summary=payload.get("final_summary", ""),
        )
