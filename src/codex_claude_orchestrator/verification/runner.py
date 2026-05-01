from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from typing import Callable
from uuid import uuid4

from codex_claude_orchestrator.core.models import VerificationKind, VerificationRecord
from codex_claude_orchestrator.core.policy_gate import PolicyGate
from codex_claude_orchestrator.state.session_recorder import SessionRecorder


VerificationCommandRunner = Callable[..., CompletedProcess[str]]


class VerificationRunner:
    def __init__(
        self,
        repo_root: Path,
        session_recorder: SessionRecorder,
        policy_gate: PolicyGate,
        timeout_seconds: int = 120,
        runner: VerificationCommandRunner | None = None,
    ):
        self._repo_root = repo_root
        self._session_recorder = session_recorder
        self._policy_gate = policy_gate
        self._timeout_seconds = timeout_seconds
        self._runner = runner or subprocess.run

    def run(self, session_id: str, turn_id: str, command: str) -> VerificationRecord:
        verification_id = f"verification-{uuid4().hex}"
        stdout_artifact_name = f"verification/{verification_id}/stdout.txt"
        stderr_artifact_name = f"verification/{verification_id}/stderr.txt"

        argv = shlex.split(command)
        decision = self._policy_gate.guard_command(argv)
        if not decision.allowed:
            reason = decision.reason or "command blocked by policy"
            stdout_path = self._session_recorder.write_text_artifact(session_id, stdout_artifact_name, "")
            stderr_path = self._session_recorder.write_text_artifact(session_id, stderr_artifact_name, f"{reason}\n")
            record = VerificationRecord(
                verification_id=verification_id,
                session_id=session_id,
                turn_id=turn_id,
                kind=VerificationKind.COMMAND,
                passed=False,
                command=command,
                exit_code=None,
                summary=f"command blocked: {reason}",
                stdout_artifact=str(stdout_path),
                stderr_artifact=str(stderr_path),
            )
            self._session_recorder.append_verification(session_id, record)
            return record

        result = self._runner(
            argv,
            cwd=self._repo_root,
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
        )
        passed = result.returncode == 0
        stdout_path = self._session_recorder.write_text_artifact(
            session_id,
            stdout_artifact_name,
            result.stdout,
        )
        stderr_path = self._session_recorder.write_text_artifact(
            session_id,
            stderr_artifact_name,
            result.stderr,
        )
        summary_status = "passed" if passed else "failed"
        record = VerificationRecord(
            verification_id=verification_id,
            session_id=session_id,
            turn_id=turn_id,
            kind=VerificationKind.COMMAND,
            passed=passed,
            command=command,
            exit_code=result.returncode,
            summary=f"command {summary_status}: exit code {result.returncode}",
            stdout_artifact=str(stdout_path),
            stderr_artifact=str(stderr_path),
        )
        self._session_recorder.append_verification(session_id, record)
        return record
