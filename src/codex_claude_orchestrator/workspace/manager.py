from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from codex_claude_orchestrator.core.models import TaskRecord, WorkspaceAllocation, WorkspaceMode


class WorkspaceManager:
    def __init__(self, state_root: Path):
        self._state_root = state_root
        self._workspace_root = state_root / "workspaces"
        self._workspace_root.mkdir(parents=True, exist_ok=True)

    def prepare(self, source_repo: Path, task: TaskRecord) -> WorkspaceAllocation:
        source_repo = source_repo.resolve()
        if task.workspace_mode is WorkspaceMode.READONLY:
            return WorkspaceAllocation(
                workspace_id=task.task_id,
                path=source_repo,
                mode=WorkspaceMode.READONLY,
                writable=False,
                baseline_snapshot=self._snapshot_tree(source_repo),
            )
        if task.workspace_mode is WorkspaceMode.SHARED:
            return WorkspaceAllocation(
                workspace_id=task.task_id,
                path=source_repo,
                mode=WorkspaceMode.SHARED,
                writable=True,
                baseline_snapshot=self._snapshot_tree(source_repo),
            )

        workspace_path = self._workspace_root / task.task_id
        if workspace_path.exists():
            shutil.rmtree(workspace_path)
        shutil.copytree(
            source_repo,
            workspace_path,
            ignore=shutil.ignore_patterns(".git", ".orchestrator", "__pycache__", ".pytest_cache"),
        )

        return WorkspaceAllocation(
            workspace_id=task.task_id,
            path=workspace_path,
            mode=WorkspaceMode.ISOLATED,
            writable=True,
            baseline_snapshot=self._snapshot_tree(workspace_path),
        )

    def detect_changes(self, allocation: WorkspaceAllocation) -> list[str]:
        current_snapshot = self._snapshot_tree(allocation.path)
        all_paths = set(allocation.baseline_snapshot) | set(current_snapshot)
        return [
            relative_path
            for relative_path in sorted(all_paths)
            if allocation.baseline_snapshot.get(relative_path) != current_snapshot.get(relative_path)
        ]

    def _snapshot_tree(self, root: Path) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for file_path in sorted(path for path in root.rglob("*") if path.is_file()):
            relative_path = file_path.relative_to(root).as_posix()
            if relative_path.startswith(".git/") or relative_path.startswith(".orchestrator/"):
                continue
            snapshot[relative_path] = self._hash_file(file_path)
        return snapshot

    def _hash_file(self, file_path: Path) -> str:
        digest = hashlib.sha256()
        digest.update(file_path.read_bytes())
        return digest.hexdigest()
