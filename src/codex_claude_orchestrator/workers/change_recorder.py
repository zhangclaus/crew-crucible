from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.crew.models import ActorType, BlackboardEntry, BlackboardEntryType
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.core.models import WorkspaceAllocation, WorkspaceMode
from codex_claude_orchestrator.workspace.worktree_manager import WorktreeManager


class WorkerChangeRecorder:
    def __init__(
        self,
        recorder: CrewRecorder,
        *,
        worktree_manager: WorktreeManager,
        entry_id_factory: Callable[[], str] | None = None,
    ):
        self._recorder = recorder
        self._worktree_manager = worktree_manager
        self._entry_id_factory = entry_id_factory or (lambda: f"entry-{uuid4().hex}")

    def record_changes(self, crew_id: str, worker_id: str, allocation: WorkspaceAllocation) -> dict:
        if allocation.mode is WorkspaceMode.WORKTREE:
            changed_files = self._worktree_manager.changed_files(allocation)
            diff_patch = self._worktree_manager.diff_patch(allocation)
        else:
            changed_files = self._snapshot_changes(allocation)
            diff_patch = ""
        artifact_name = f"workers/{worker_id}/changes.json"
        diff_artifact = f"workers/{worker_id}/diff.patch"
        payload = {
            "crew_id": crew_id,
            "worker_id": worker_id,
            "branch": allocation.branch,
            "base_ref": allocation.base_ref,
            "changed_files": changed_files,
            "diff_artifact": diff_artifact,
            "artifact": artifact_name,
        }
        self._recorder.write_text_artifact(crew_id, artifact_name, json.dumps(payload, indent=2, ensure_ascii=False))
        self._recorder.write_text_artifact(crew_id, diff_artifact, diff_patch)
        self._recorder.append_blackboard(
            crew_id,
            BlackboardEntry(
                entry_id=self._entry_id_factory(),
                crew_id=crew_id,
                task_id=None,
                actor_type=ActorType.WORKER,
                actor_id=worker_id,
                type=BlackboardEntryType.PATCH,
                content=f"Worker {worker_id} changed {len(changed_files)} file(s).",
                evidence_refs=[artifact_name, diff_artifact, *changed_files],
                confidence=0.8,
            ),
        )
        return payload

    def _snapshot_changes(self, allocation: WorkspaceAllocation) -> list[str]:
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
