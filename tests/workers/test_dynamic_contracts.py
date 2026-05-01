from pathlib import Path

from codex_claude_orchestrator.state.blackboard import BlackboardStore
from codex_claude_orchestrator.crew.models import (
    AuthorityLevel,
    CrewRecord,
    CrewStatus,
    WorkerContract,
    WorkspacePolicy,
)
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.core.models import WorkspaceAllocation, WorkspaceMode
from codex_claude_orchestrator.workers.pool import WorkerPool


class FakeWorktreeManager:
    def __init__(self):
        self.prepared = []

    def prepare(self, *, repo_root, crew_id, worker_id, allow_dirty_base=False):
        self.prepared.append({"repo_root": repo_root, "crew_id": crew_id, "worker_id": worker_id})
        path = repo_root.parent / ".orchestrator" / "worktrees" / crew_id / worker_id
        path.mkdir(parents=True, exist_ok=True)
        return WorkspaceAllocation(
            workspace_id=f"{crew_id}-{worker_id}",
            path=path,
            mode=WorkspaceMode.WORKTREE,
            writable=True,
            branch=f"codex/{crew_id}-{worker_id}",
        )


class FakeNativeSession:
    def __init__(self):
        self.starts = []

    def start(self, **kwargs):
        self.starts.append(kwargs)
        return {
            "native_session_id": f"native-{kwargs['worker_id']}",
            "terminal_session": f"crew-1-{kwargs['worker_id']}",
            "terminal_pane": f"crew-1-{kwargs['worker_id']}:claude.0",
            "transcript_artifact": str(kwargs["transcript_path"]),
            "turn_marker": "<<<CODEX_TURN_DONE>>>",
        }


def test_worker_pool_ensure_worker_spawns_contract_worker_and_records_artifacts(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Fix tests", repo=repo_root)
    recorder.start_crew(crew)
    recorder.update_crew("crew-1", {"status": CrewStatus.RUNNING.value, "active_worker_ids": []})
    native = FakeNativeSession()
    pool = WorkerPool(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        worktree_manager=FakeWorktreeManager(),
        native_session=native,
        worker_id_factory=lambda role: "worker-source",
        entry_id_factory=lambda: "entry-contract",
    )
    contract = WorkerContract(
        contract_id="contract-source",
        label="targeted-code-editor",
        mission="Fix tests with the smallest patch.",
        required_capabilities=["inspect_code", "edit_source", "edit_tests", "run_verification"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
        protocol_refs=["task_confirmation"],
        spawn_reason="goal requires source edits",
    )

    worker = pool.ensure_worker(repo_root=repo_root, crew=crew, contract=contract)
    details = recorder.read_crew("crew-1")

    assert worker.worker_id == "worker-source"
    assert worker.label == "targeted-code-editor"
    assert worker.contract_id == "contract-source"
    assert worker.capabilities == ["inspect_code", "edit_source", "edit_tests", "run_verification"]
    assert worker.authority_level == AuthorityLevel.SOURCE_WRITE
    assert worker.workspace_mode == WorkspaceMode.WORKTREE
    assert details["crew"]["active_worker_ids"] == ["worker-source"]
    assert details["worker_contracts"][0]["contract_id"] == "contract-source"
    assert "contracts/contract-source.json" in details["artifacts"]
    assert "workers/worker-source/onboarding_prompt.md" in details["artifacts"]
    assert native.starts[0]["role"] == "targeted-code-editor"
    assert "Capability contract: targeted-code-editor" in native.starts[0]["instructions"]
    assert "If Codex sends a per-turn marker later, print the latest per-turn marker instead." in native.starts[0]["instructions"]
    assert "## Capability: inspect_code" in native.starts[0]["instructions"]
    assert "## Capability: edit_source" in native.starts[0]["instructions"]
    assert "## Protocol: task_confirmation" in native.starts[0]["instructions"]


def test_worker_pool_ensure_worker_reuses_compatible_running_worker(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Fix tests", repo=repo_root)
    recorder.start_crew(crew)
    recorder.update_crew("crew-1", {"status": CrewStatus.RUNNING.value, "active_worker_ids": []})
    native = FakeNativeSession()
    pool = WorkerPool(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        worktree_manager=FakeWorktreeManager(),
        native_session=native,
        worker_id_factory=lambda role: "worker-source",
    )
    first_contract = WorkerContract(
        contract_id="contract-source-1",
        label="targeted-code-editor",
        mission="Fix tests.",
        required_capabilities=["inspect_code", "edit_source"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
    )
    second_contract = WorkerContract(
        contract_id="contract-source-2",
        label="second-source-edit",
        mission="Continue fixing tests.",
        required_capabilities=["inspect_code"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
    )

    first = pool.ensure_worker(repo_root=repo_root, crew=crew, contract=first_contract)
    second = pool.ensure_worker(repo_root=repo_root, crew=crew, contract=second_contract)

    assert first.worker_id == second.worker_id
    assert len(native.starts) == 1
