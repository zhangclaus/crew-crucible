from pathlib import Path
from subprocess import CompletedProcess
import subprocess

import pytest

from codex_claude_orchestrator.core.models import WorkspaceMode
from codex_claude_orchestrator.workspace.worktree_manager import DirtyWorktreeError, WorktreeManager


class FakeGitRunner:
    def __init__(self, dirty_output: str = ""):
        self.dirty_output = dirty_output
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return CompletedProcess(command, 0, stdout="true\n", stderr="")
        if command[:2] == ["git", "status"]:
            return CompletedProcess(command, 0, stdout=self.dirty_output, stderr="")
        if command[:2] == ["git", "rev-parse"]:
            return CompletedProcess(command, 0, stdout="base-sha\n", stderr="")
        if command[:2] == ["git", "diff"]:
            return CompletedProcess(command, 0, stdout="src/app.py\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")


def test_worktree_manager_creates_branch_worktree_for_clean_repo(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    runner = FakeGitRunner()
    manager = WorktreeManager(
        state_root=tmp_path / ".orchestrator",
        runner=runner,
        branch_name_factory=lambda crew_id, worker_id: f"codex/{crew_id}-{worker_id}",
    )

    allocation = manager.prepare(repo_root=repo_root, crew_id="crew-1", worker_id="worker-implementer")
    changed = manager.changed_files(allocation)
    patch = manager.diff_patch(allocation)

    assert allocation.mode == WorkspaceMode.WORKTREE
    assert allocation.path == tmp_path / ".orchestrator" / "worktrees" / "crew-1" / "worker-implementer"
    assert allocation.branch == "codex/crew-1-worker-implementer"
    assert allocation.base_ref == "base-sha"
    assert changed == ["src/app.py"]
    assert patch == "src/app.py\n"
    assert ["git", "worktree", "add", "-b", "codex/crew-1-worker-implementer", str(allocation.path), "base-sha"] in [
        call[0] for call in runner.calls
    ]


def test_worktree_manager_blocks_dirty_repo_by_default(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    manager = WorktreeManager(
        state_root=tmp_path / ".orchestrator",
        runner=FakeGitRunner(dirty_output=" M app.py\n"),
    )

    with pytest.raises(DirtyWorktreeError, match="repo has uncommitted changes"):
        manager.prepare(repo_root=repo_root, crew_id="crew-1", worker_id="worker-implementer")


def test_worktree_manager_smoke_creates_real_git_worktree_and_detects_uncommitted_changes(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test User")
    (repo_root / "app.py").write_text("one\n", encoding="utf-8")
    _git(repo_root, "add", "app.py")
    _git(repo_root, "commit", "-q", "-m", "init")

    manager = WorktreeManager(state_root=repo_root / ".orchestrator")
    allocation = manager.prepare(repo_root=repo_root, crew_id="crew-1", worker_id="worker-implementer")
    (allocation.path / "app.py").write_text("two\n", encoding="utf-8")
    (allocation.path / "new.py").write_text("new\n", encoding="utf-8")

    changed = manager.changed_files(allocation)

    assert allocation.path.is_dir()
    assert allocation.branch == "codex/crew-1-worker-implementer"
    assert changed == ["app.py", "new.py"]


def test_worktree_manager_allow_dirty_base_copies_untracked_files_to_worktree(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test User")
    (repo_root / "app.py").write_text("one\n", encoding="utf-8")
    _git(repo_root, "add", "app.py")
    _git(repo_root, "commit", "-q", "-m", "init")
    (repo_root / "notes.md").write_text("untracked context\n", encoding="utf-8")

    manager = WorktreeManager(state_root=repo_root / ".orchestrator")
    allocation = manager.prepare(
        repo_root=repo_root,
        crew_id="crew-1",
        worker_id="worker-implementer",
        allow_dirty_base=True,
    )

    assert (allocation.path / "notes.md").read_text(encoding="utf-8") == "untracked context\n"
    assert not (allocation.path / ".orchestrator").exists()
    assert "workers/worker-implementer/dirty-base-untracked-files.txt" in [
        path.relative_to(repo_root / ".orchestrator" / "crews" / "crew-1" / "artifacts").as_posix()
        for path in (repo_root / ".orchestrator" / "crews" / "crew-1" / "artifacts").rglob("*")
        if path.is_file()
    ]


def test_worktree_manager_diff_patch_includes_untracked_files(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test User")
    (repo_root / "app.py").write_text("one\n", encoding="utf-8")
    _git(repo_root, "add", "app.py")
    _git(repo_root, "commit", "-q", "-m", "init")

    manager = WorktreeManager(state_root=repo_root / ".orchestrator")
    allocation = manager.prepare(repo_root=repo_root, crew_id="crew-1", worker_id="worker-implementer")
    (allocation.path / "new.py").write_text("new file\n", encoding="utf-8")

    patch = manager.diff_patch(allocation)

    assert "diff --git" in patch
    assert "new.py" in patch
    assert "+new file" in patch


def test_worktree_manager_cleanup_keeps_worktree_by_default_and_removes_clean_worktree(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    worktree_path = tmp_path / ".orchestrator" / "worktrees" / "crew-1" / "worker-1"
    worktree_path.mkdir(parents=True)
    runner = FakeGitRunner()
    manager = WorktreeManager(state_root=tmp_path / ".orchestrator", runner=runner)
    allocation = type("Allocation", (), {"path": worktree_path, "mode": WorkspaceMode.WORKTREE})()

    kept = manager.cleanup(repo_root=repo_root, allocation=allocation, remove=False)
    removed = manager.cleanup(repo_root=repo_root, allocation=allocation, remove=True)

    assert kept == {"removed": False, "reason": "keep policy"}
    assert removed == {"removed": True, "path": str(worktree_path)}
    assert ["git", "worktree", "remove", str(worktree_path)] in [call[0] for call in runner.calls]


def test_worktree_manager_cleanup_refuses_dirty_worktree_removal(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    worktree_path = tmp_path / ".orchestrator" / "worktrees" / "crew-1" / "worker-1"
    worktree_path.mkdir(parents=True)
    manager = WorktreeManager(state_root=tmp_path / ".orchestrator", runner=FakeGitRunner(dirty_output=" M app.py\n"))
    allocation = type("Allocation", (), {"path": worktree_path, "mode": WorkspaceMode.WORKTREE})()

    with pytest.raises(DirtyWorktreeError, match="refusing to remove dirty worktree"):
        manager.cleanup(repo_root=repo_root, allocation=allocation, remove=True)


def _git(repo_root: Path, *args: str) -> CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
