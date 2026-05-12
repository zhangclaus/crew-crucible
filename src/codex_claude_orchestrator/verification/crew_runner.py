from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess
from uuid import uuid4

from codex_claude_orchestrator.crew.models import ActorType, BlackboardEntry, BlackboardEntryType
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.core.policy_gate import PolicyGate


VerificationCommandRunner = Callable[..., CompletedProcess[str]]


class CrewVerificationRunner:
    def __init__(
        self,
        *,
        repo_root: Path,
        recorder: CrewRecorder,
        policy_gate: PolicyGate,
        runner: VerificationCommandRunner | None = None,
        timeout_seconds: int = 120,
        verification_id_factory: Callable[[], str] | None = None,
        entry_id_factory: Callable[[], str] | None = None,
    ):
        self._repo_root = repo_root
        self._recorder = recorder
        self._policy_gate = policy_gate
        self._runner = runner or subprocess.run
        self._timeout_seconds = timeout_seconds
        self._verification_id_factory = verification_id_factory or (lambda: f"verification-{uuid4().hex}")
        self._entry_id_factory = entry_id_factory or (lambda: f"entry-{uuid4().hex}")

    def run(
        self,
        crew_id: str,
        command: str,
        *,
        cwd: Path | None = None,
        target_worker_id: str | None = None,
    ) -> dict:
        verification_id = self._verification_id_factory()
        stdout_artifact = f"verification/{verification_id}/stdout.txt"
        stderr_artifact = f"verification/{verification_id}/stderr.txt"
        verification_cwd = cwd or self._repo_root
        argv = self._resolve_repo_relative_executable(shlex.split(command), verification_cwd)
        decision = self._policy_gate.guard_command(argv)
        if not decision.allowed:
            reason = decision.reason or "command blocked by policy"
            stdout_path = self._recorder.write_text_artifact(crew_id, stdout_artifact, "")
            stderr_path = self._recorder.write_text_artifact(crew_id, stderr_artifact, f"{reason}\n")
            payload = {
                "verification_id": verification_id,
                "command": command,
                "passed": False,
                "exit_code": None,
                "summary": f"command blocked: {reason}",
                "cwd": str(verification_cwd),
                "target_worker_id": target_worker_id,
                "stdout_artifact": str(stdout_path),
                "stderr_artifact": str(stderr_path),
            }
            self._record_blackboard(crew_id, payload)
            return payload

        result = self._runner(
            argv,
            shell=False,
            cwd=verification_cwd,
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
        )
        passed = result.returncode == 0
        stdout_path = self._recorder.write_text_artifact(crew_id, stdout_artifact, result.stdout)
        stderr_path = self._recorder.write_text_artifact(crew_id, stderr_artifact, result.stderr)
        summary_status = "passed" if passed else "failed"
        payload = {
            "verification_id": verification_id,
            "command": command,
            "passed": passed,
            "exit_code": result.returncode,
            "summary": f"command {summary_status}: exit code {result.returncode}",
            "cwd": str(verification_cwd),
            "target_worker_id": target_worker_id,
            "stdout_artifact": str(stdout_path),
            "stderr_artifact": str(stderr_path),
        }
        self._record_blackboard(crew_id, payload)
        return payload

    def _resolve_repo_relative_executable(self, argv: list[str], cwd: Path) -> list[str]:
        if not argv:
            return argv
        executable = Path(argv[0])
        if executable.is_absolute() or not self._is_relative_path_executable(argv[0]):
            return argv
        if (cwd / executable).exists():
            return argv
        repo_executable = self._repo_root / executable
        if repo_executable.exists():
            return [str(repo_executable), *argv[1:]]
        return argv

    def _is_relative_path_executable(self, value: str) -> bool:
        return "/" in value or value.startswith(".")

    def _record_blackboard(self, crew_id: str, payload: dict) -> None:
        self._recorder.append_blackboard(
            crew_id,
            BlackboardEntry(
                entry_id=self._entry_id_factory(),
                crew_id=crew_id,
                task_id=None,
                actor_type=ActorType.CODEX,
                actor_id="codex",
                type=BlackboardEntryType.VERIFICATION,
                content=payload["summary"],
                evidence_refs=[payload["stdout_artifact"], payload["stderr_artifact"]],
                confidence=1.0,
            ),
        )
