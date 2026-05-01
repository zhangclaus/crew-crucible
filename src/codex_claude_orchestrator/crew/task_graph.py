from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from codex_claude_orchestrator.crew.models import CrewTaskRecord, CrewTaskStatus, WorkerRole
from codex_claude_orchestrator.crew.models import AuthorityLevel, WorkerContract
from codex_claude_orchestrator.core.models import utc_now


class TaskGraphPlanner:
    def __init__(self, task_id_factory: Callable[[WorkerRole], str] | None = None):
        self._task_id_factory = task_id_factory or (lambda role: f"task-{role.value}-{uuid4().hex[:8]}")

    def default_graph(self, crew_id: str, goal: str) -> list[CrewTaskRecord]:
        explorer_id = self._task_id_factory(WorkerRole.EXPLORER)
        implementer_id = self._task_id_factory(WorkerRole.IMPLEMENTER)
        reviewer_id = self._task_id_factory(WorkerRole.REVIEWER)
        return [
            CrewTaskRecord(
                task_id=explorer_id,
                crew_id=crew_id,
                title="Explore repo",
                instructions=f"Read the repository for this goal and report facts, risks, and relevant files: {goal}",
                role_required=WorkerRole.EXPLORER,
                expected_outputs=["facts", "risks", "relevant_files"],
            ),
            CrewTaskRecord(
                task_id=implementer_id,
                crew_id=crew_id,
                title="Implement change",
                instructions=(
                    "Wait for Codex to send explorer findings or an explicit begin message before editing. "
                    f"Then implement the requested change in your worker worktree: {goal}"
                ),
                role_required=WorkerRole.IMPLEMENTER,
                depends_on=[explorer_id],
                expected_outputs=["patch", "changed_files", "verification_notes"],
            ),
            CrewTaskRecord(
                task_id=reviewer_id,
                crew_id=crew_id,
                title="Review change",
                instructions=(
                    "Wait for Codex to send patch evidence before reviewing. "
                    f"Then review the proposed patch and evidence for this goal: {goal}"
                ),
                role_required=WorkerRole.REVIEWER,
                depends_on=[implementer_id],
                expected_outputs=["review", "risks", "acceptance_recommendation"],
            ),
        ]

    def assign(self, tasks: list[CrewTaskRecord], task_id: str, worker_id: str) -> list[CrewTaskRecord]:
        for task in tasks:
            if task.task_id == task_id:
                task.owner_worker_id = worker_id
                task.status = CrewTaskStatus.ASSIGNED
                task.updated_at = utc_now()
        return tasks

    def task_for_contract(self, crew_id: str, contract: WorkerContract) -> CrewTaskRecord:
        return CrewTaskRecord(
            task_id=f"task-{contract.contract_id}",
            crew_id=crew_id,
            title=contract.label,
            instructions=contract.mission,
            role_required=self.legacy_role_for_contract(contract),
            expected_outputs=contract.expected_outputs,
            acceptance_criteria=contract.acceptance_criteria,
            contract_id=contract.contract_id,
            required_capabilities=contract.required_capabilities,
            authority_level=contract.authority_level,
        )

    def legacy_role_for_contract(self, contract: WorkerContract) -> WorkerRole:
        if contract.authority_level in {AuthorityLevel.SOURCE_WRITE, AuthorityLevel.TEST_WRITE, AuthorityLevel.STATE_WRITE}:
            return WorkerRole.IMPLEMENTER
        if "review_patch" in contract.required_capabilities:
            return WorkerRole.REVIEWER
        return WorkerRole.EXPLORER
