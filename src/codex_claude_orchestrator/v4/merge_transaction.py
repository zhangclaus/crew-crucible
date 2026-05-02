"""V4 merge transaction for accepting crew work safely."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import subprocess
from subprocess import CompletedProcess
from uuid import uuid4

from codex_claude_orchestrator.crew.models import CrewStatus
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.v4.event_store_protocol import EventStore


CommandRunner = Callable[..., CompletedProcess[str]]
StopWorkers = Callable[..., dict]


@dataclass(frozen=True, slots=True)
class WorkerPatch:
    worker_id: str
    base_ref: str
    changed_files: list[str]
    diff_artifact: str
    patch: str
    patch_paths: list[str]


class V4MergeTransaction:
    def __init__(
        self,
        *,
        repo_root: Path,
        recorder: CrewRecorder,
        event_store: EventStore,
        git_runner: CommandRunner | None = None,
        command_runner: CommandRunner | None = None,
        stop_workers: StopWorkers | None = None,
    ) -> None:
        self._repo_root = repo_root.resolve()
        self._recorder = recorder
        self._events = event_store
        self._git_runner = git_runner or subprocess.run
        self._command_runner = command_runner or subprocess.run
        self._stop_workers = stop_workers or (lambda **_: {})

    def accept(
        self,
        *,
        crew_id: str,
        summary: str,
        verification_commands: list[str],
    ) -> dict:
        if not verification_commands:
            return self._blocked(crew_id, reason="verification command required")

        patches = self._load_worker_patches(crew_id)
        if not patches:
            return self._blocked(crew_id, reason="no worker patches recorded")

        conflict_paths = self._conflict_paths(patches)
        if conflict_paths:
            return self._blocked(
                crew_id,
                reason="multiple workers changed the same path",
                conflicts=conflict_paths,
            )

        outside_scope = self._paths_outside_recorded_changes(patches)
        if outside_scope:
            return self._blocked(
                crew_id,
                reason="patch touches paths outside recorded changed_files",
                paths=outside_scope,
            )

        base_ref = self._single_base_ref(patches)
        if not base_ref:
            return self._blocked(crew_id, reason="worker patch base_ref is missing or inconsistent")

        initial_dirty = self._main_dirty()
        if initial_dirty:
            return self._blocked(
                crew_id,
                reason="main workspace has uncommitted changes",
                dirty=initial_dirty,
            )
        current_head = self._main_head()
        if current_head != base_ref:
            return self._blocked(
                crew_id,
                reason="main workspace base ref changed",
                expected_base_ref=base_ref,
                actual_head=current_head,
            )

        self._append_event(crew_id, "merge.started", {"base_ref": base_ref})
        integration_path = self._integration_path(crew_id)
        combined_patch = self._write_combined_patch(crew_id, patches)
        try:
            self._git(["worktree", "add", "--detach", str(integration_path), base_ref], cwd=self._repo_root)
            check_result = self._git(["apply", "--check", str(combined_patch)], cwd=integration_path, check=False)
            if check_result.returncode != 0:
                return self._blocked(
                    crew_id,
                    reason="patch failed to apply to integration worktree",
                    stderr=check_result.stderr,
                )
            apply_result = self._git(["apply", str(combined_patch)], cwd=integration_path, check=False)
            if apply_result.returncode != 0:
                return self._blocked(
                    crew_id,
                    reason="patch apply failed in integration worktree",
                    stderr=apply_result.stderr,
                )

            verification_results = self._run_verification(
                verification_commands,
                cwd=integration_path,
            )
            self._append_event(
                crew_id,
                "merge.verified",
                {"verification": verification_results},
            )
            failed = [result for result in verification_results if not result["passed"]]
            if failed:
                return self._blocked(
                    crew_id,
                    reason="final verification failed",
                    verification=verification_results,
                )

            final_dirty = self._main_dirty()
            if final_dirty:
                return self._blocked(
                    crew_id,
                    reason="main workspace changed during merge transaction",
                    dirty=final_dirty,
                )
            final_head = self._main_head()
            if final_head != base_ref:
                return self._blocked(
                    crew_id,
                    reason="main workspace base ref changed during merge transaction",
                    expected_base_ref=base_ref,
                    actual_head=final_head,
                )

            main_check = self._git(["apply", "--check", str(combined_patch)], cwd=self._repo_root, check=False)
            if main_check.returncode != 0:
                return self._blocked(
                    crew_id,
                    reason="patch failed final main workspace check",
                    stderr=main_check.stderr,
                )
            main_apply = self._git(["apply", str(combined_patch)], cwd=self._repo_root, check=False)
            if main_apply.returncode != 0:
                return self._blocked(
                    crew_id,
                    reason="patch failed to apply to main workspace",
                    stderr=main_apply.stderr,
                )

            self._append_event(
                crew_id,
                "merge.applied",
                {
                    "base_ref": base_ref,
                    "patch_artifact": combined_patch.name,
                    "changed_files": sorted({path for patch in patches for path in patch.patch_paths}),
                },
            )
            self._recorder.finalize_crew(crew_id, CrewStatus.ACCEPTED, summary)
            stop_result = self._stop_workers(repo_root=self._repo_root, crew_id=crew_id)
            self._append_event(crew_id, "crew.accepted", {"summary": summary})
            return {
                "crew_id": crew_id,
                "status": CrewStatus.ACCEPTED.value,
                "summary": summary,
                "merge": {
                    "status": "applied",
                    "base_ref": base_ref,
                    "verification": verification_results,
                },
                "stop": stop_result,
            }
        finally:
            if integration_path.exists():
                self._git(["worktree", "remove", "--force", str(integration_path)], cwd=self._repo_root, check=False)

    def _load_worker_patches(self, crew_id: str) -> list[WorkerPatch]:
        details = self._recorder.read_crew(crew_id)
        artifact_root = self._recorder._crew_dir(crew_id) / "artifacts"
        patches: list[WorkerPatch] = []
        for artifact in details["artifacts"]:
            if not artifact.endswith("/changes.json"):
                continue
            changes = json.loads((artifact_root / artifact).read_text(encoding="utf-8"))
            diff_artifact = changes.get("diff_artifact", "")
            patch = (artifact_root / diff_artifact).read_text(encoding="utf-8") if diff_artifact else ""
            patch_paths = _patch_paths(patch)
            patches.append(
                WorkerPatch(
                    worker_id=changes.get("worker_id", ""),
                    base_ref=changes.get("base_ref", ""),
                    changed_files=_string_list(changes.get("changed_files", [])),
                    diff_artifact=diff_artifact,
                    patch=patch,
                    patch_paths=patch_paths,
                )
            )
        return patches

    def _write_combined_patch(self, crew_id: str, patches: list[WorkerPatch]) -> Path:
        content = "\n".join(patch.patch for patch in patches if patch.patch.strip())
        path = self._recorder.write_text_artifact(
            crew_id,
            f"merge/combined-{uuid4().hex}.patch",
            content,
        )
        return path

    def _integration_path(self, crew_id: str) -> Path:
        path = self._recorder._state_root / "v4" / "integration" / crew_id / f"integration-{uuid4().hex[:8]}"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _run_verification(self, commands: list[str], *, cwd: Path) -> list[dict]:
        results = []
        for index, command in enumerate(commands, start=1):
            try:
                argv = shlex.split(command)
                result = self._command_runner(
                    argv,
                    cwd=cwd,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                passed = result.returncode == 0
                results.append(
                    {
                        "verification_id": f"merge-verification-{index}",
                        "command": command,
                        "passed": passed,
                        "exit_code": result.returncode,
                        "summary": "command passed" if passed else "command failed",
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "verification_id": f"merge-verification-{index}",
                        "command": command,
                        "passed": False,
                        "exit_code": None,
                        "summary": f"command setup failed: {exc}",
                        "stdout": "",
                        "stderr": f"{exc}\n",
                    }
                )
        return results

    def _single_base_ref(self, patches: list[WorkerPatch]) -> str:
        base_refs = {patch.base_ref for patch in patches if patch.base_ref}
        return next(iter(base_refs)) if len(base_refs) == 1 else ""

    def _paths_outside_recorded_changes(self, patches: list[WorkerPatch]) -> list[str]:
        outside: list[str] = []
        for patch in patches:
            allowed = set(patch.changed_files)
            outside.extend(path for path in patch.patch_paths if path not in allowed)
        return sorted(set(outside))

    def _conflict_paths(self, patches: list[WorkerPatch]) -> list[dict]:
        owners: dict[str, list[str]] = {}
        for patch in patches:
            for path in patch.patch_paths:
                owners.setdefault(path, []).append(patch.worker_id)
        return [
            {"path": path, "workers": sorted(set(path_owners))}
            for path, path_owners in sorted(owners.items())
            if len(set(path_owners)) > 1
        ]

    def _main_dirty(self) -> str:
        return self._git(["status", "--porcelain"], cwd=self._repo_root).stdout.strip()

    def _main_head(self) -> str:
        return self._git(["rev-parse", "HEAD"], cwd=self._repo_root).stdout.strip()

    def _blocked(self, crew_id: str, *, reason: str, **payload) -> dict:
        blocked_payload = {"reason": reason, **payload}
        self._append_event(crew_id, "merge.blocked", blocked_payload)
        return {"crew_id": crew_id, "status": "blocked", "reason": reason, **payload}

    def _append_event(self, crew_id: str, event_type: str, payload: dict) -> None:
        self._events.append(
            stream_id=crew_id,
            type=event_type,
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/{event_type}/{uuid4().hex}",
            payload=payload,
        )

    def _git(self, args: list[str], *, cwd: Path, check: bool = True) -> CompletedProcess[str]:
        result = self._git_runner(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                output=result.stdout,
                stderr=result.stderr,
            )
        return result


def _patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                paths.extend([_clean_diff_path(parts[2]), _clean_diff_path(parts[3])])
        elif line.startswith(("--- ", "+++ ")):
            parts = line.split()
            if len(parts) >= 2:
                paths.append(_clean_diff_path(parts[1]))
    safe_paths = [path for path in paths if path and _safe_relative_path(path)]
    return sorted(set(safe_paths))


def _clean_diff_path(value: str) -> str:
    if value in {"/dev/null", "a/dev/null", "b/dev/null"}:
        return ""
    if value.startswith(("a/", "b/")):
        return value[2:]
    return value


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


__all__ = ["V4MergeTransaction"]
