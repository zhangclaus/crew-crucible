from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from uuid import uuid4

from codex_claude_orchestrator.core.models import WorkspaceAllocation, WorkspaceMode


GitRunner = Callable[..., CompletedProcess[str]]


class DirtyWorktreeError(RuntimeError):
    pass


class NotGitRepositoryError(RuntimeError):
    pass


class WorktreeManager:
    def __init__(
        self,
        state_root: Path,
        *,
        runner: GitRunner | None = None,
        branch_name_factory: Callable[[str, str], str] | None = None,
    ):
        self._state_root = state_root
        self._worktrees_root = state_root / "worktrees"
        self._runner = runner or subprocess.run
        self._branch_name_factory = branch_name_factory or self._default_branch_name

    def prepare(
        self,
        *,
        repo_root: Path,
        crew_id: str,
        worker_id: str,
        allow_dirty_base: bool = False,
    ) -> WorkspaceAllocation:
        repo_root = repo_root.resolve()
        self._ensure_git_repo(repo_root)
        dirty = self._git(["status", "--porcelain"], cwd=repo_root).stdout.strip()
        if dirty and not allow_dirty_base:
            raise DirtyWorktreeError(f"repo has uncommitted changes:\n{dirty}")

        base_ref = self._git(["rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
        branch = self._branch_name_factory(crew_id, worker_id)
        worktree_path = self._worktrees_root / crew_id / worker_id
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        if worktree_path.exists():
            raise FileExistsError(f"worktree already exists: {worktree_path}")

        branch = self._add_worktree(repo_root, worktree_path, branch, base_ref)
        base_patch_artifact = ""
        if dirty and allow_dirty_base:
            base_patch_artifact = self._write_and_apply_dirty_patch(repo_root, worktree_path, crew_id, worker_id)

        return WorkspaceAllocation(
            workspace_id=f"{crew_id}-{worker_id}",
            path=worktree_path,
            mode=WorkspaceMode.WORKTREE,
            writable=True,
            branch=branch,
            base_ref=base_ref,
            base_patch_artifact=base_patch_artifact,
        )

    def changed_files(self, allocation: WorkspaceAllocation) -> list[str]:
        if not allocation.base_ref:
            return []
        diff = self._git(["diff", "--name-only", allocation.base_ref], cwd=allocation.path).stdout.splitlines()
        untracked = self._git(["ls-files", "--others", "--exclude-standard"], cwd=allocation.path).stdout.splitlines()
        return sorted({line for line in [*diff, *untracked] if line.strip()})

    def diff_patch(self, allocation: WorkspaceAllocation) -> str:
        if not allocation.base_ref:
            return ""
        tracked_patch = self._git(["diff", "--binary", allocation.base_ref], cwd=allocation.path).stdout
        untracked_patches = [
            self._git(["diff", "--binary", "--no-index", "--", "/dev/null", path], cwd=allocation.path, check=False).stdout
            for path in self._untracked_files(allocation.path)
        ]
        return "\n".join(part for part in [tracked_patch, *untracked_patches] if part)

    def cleanup(self, *, repo_root: Path, allocation: WorkspaceAllocation, remove: bool = False) -> dict[str, object]:
        if allocation.mode is not WorkspaceMode.WORKTREE:
            return {"removed": False, "reason": "not a worktree"}
        if not remove:
            return {"removed": False, "reason": "keep policy"}
        dirty = self._git(["status", "--porcelain"], cwd=allocation.path).stdout.strip()
        if dirty:
            raise DirtyWorktreeError(f"refusing to remove dirty worktree {allocation.path}:\n{dirty}")
        self._git(["worktree", "remove", str(allocation.path)], cwd=repo_root)
        return {"removed": True, "path": str(allocation.path)}

    def _ensure_git_repo(self, repo_root: Path) -> None:
        result = self._git(["rev-parse", "--is-inside-work-tree"], cwd=repo_root, check=False)
        if result.returncode != 0 or result.stdout.strip() != "true":
            raise NotGitRepositoryError(f"not a git repository: {repo_root}")

    def _add_worktree(self, repo_root: Path, worktree_path: Path, branch: str, base_ref: str) -> str:
        result = self._git(["worktree", "add", "-b", branch, str(worktree_path), base_ref], cwd=repo_root, check=False)
        if result.returncode == 0:
            return branch
        if "already exists" not in result.stderr.lower():
            raise CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)

        suffixed = f"{branch}-{uuid4().hex[:6]}"
        self._git(["worktree", "add", "-b", suffixed, str(worktree_path), base_ref], cwd=repo_root)
        return suffixed

    def _write_and_apply_dirty_patch(self, repo_root: Path, worktree_path: Path, crew_id: str, worker_id: str) -> str:
        patch = self._git(["diff", "--binary", "HEAD"], cwd=repo_root).stdout
        patch_path = self._state_root / "crews" / crew_id / "artifacts" / "workers" / worker_id / "dirty-base.patch"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(patch, encoding="utf-8")
        if patch.strip():
            self._git(["apply", str(patch_path)], cwd=worktree_path)
        self._copy_untracked_files(repo_root, worktree_path, crew_id, worker_id)
        return f"workers/{worker_id}/dirty-base.patch"

    def _copy_untracked_files(self, repo_root: Path, worktree_path: Path, crew_id: str, worker_id: str) -> None:
        untracked = self._untracked_files(repo_root)
        manifest_path = (
            self._state_root
            / "crews"
            / crew_id
            / "artifacts"
            / "workers"
            / worker_id
            / "dirty-base-untracked-files.txt"
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("\n".join(untracked) + ("\n" if untracked else ""), encoding="utf-8")
        for relative_path in untracked:
            source = repo_root / relative_path
            destination = worktree_path / relative_path
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    def _untracked_files(self, repo_root: Path) -> list[str]:
        result = self._git(["ls-files", "--others", "--exclude-standard", "-z"], cwd=repo_root, check=False)
        if result.returncode != 0:
            return []
        return sorted(
            path
            for path in result.stdout.split("\0")
            if path and not path.startswith(".orchestrator/") and not path.startswith(".git/")
        )

    def _git(self, args: list[str], *, cwd: Path, check: bool = True) -> CompletedProcess[str]:
        result = self._runner(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
        return result

    def _default_branch_name(self, crew_id: str, worker_id: str) -> str:
        raw = f"codex/{crew_id}-{worker_id}"
        return re.sub(r"[^A-Za-z0-9._/-]+", "-", raw).strip("-")
