from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from codex_claude_orchestrator.core.policy_gate import PolicyGate
from codex_claude_orchestrator.v4.artifacts import ArtifactStore


class VerificationAdapter:
    def __init__(self, *, artifact_store: ArtifactStore, timeout_seconds: float = 120):
        self._artifacts = artifact_store
        self._repo_root = Path.cwd().resolve()
        self._timeout_seconds = timeout_seconds

    def run(self, *, command: str, cwd: Path, verification_id: str) -> dict:
        try:
            argv = shlex.split(command)
            if not argv:
                return self._failed_result(
                    verification_id=verification_id,
                    command=command,
                    summary="command setup failed: empty command",
                    stderr="command setup failed: empty command\n",
                )

            PolicyGate().guard_command(argv)
            argv = self._resolve_repo_relative_executable(argv, cwd)
            result = subprocess.run(
                argv,
                shell=False,
                cwd=cwd,
                text=True,
                capture_output=True,
                check=False,
                timeout=self._timeout_seconds,
            )
        except ValueError as error:
            return self._failed_result(
                verification_id=verification_id,
                command=command,
                summary=f"command setup failed: {error}",
                stderr=f"command setup failed: {error}\n",
            )
        except OSError as error:
            return self._failed_result(
                verification_id=verification_id,
                command=command,
                summary=f"command setup failed: {error}",
                stderr=f"command setup failed: {error}\n",
            )
        except subprocess.TimeoutExpired as error:
            stdout = self._timeout_output(error.stdout)
            stderr = self._timeout_output(error.stderr)
            message = f"command timed out after {self._format_timeout()}s"
            if stderr and not stderr.endswith("\n"):
                stderr += "\n"
            stderr += f"{message}\n"
            return self._failed_result(
                verification_id=verification_id,
                command=command,
                summary=message,
                stdout=stdout,
                stderr=stderr,
            )

        stdout_artifact = self._artifacts.write_text(
            f"verification/{verification_id}/stdout.txt",
            result.stdout,
        )
        stderr_artifact = self._artifacts.write_text(
            f"verification/{verification_id}/stderr.txt",
            result.stderr,
        )
        return {
            "verification_id": verification_id,
            "command": command,
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "summary": f"command {'passed' if result.returncode == 0 else 'failed'}: exit code {result.returncode}",
            "stdout_artifact": stdout_artifact.path,
            "stderr_artifact": stderr_artifact.path,
        }

    def _failed_result(
        self,
        *,
        verification_id: str,
        command: str,
        summary: str,
        stdout: str = "",
        stderr: str,
    ) -> dict:
        stdout_artifact = self._artifacts.write_text(
            f"verification/{verification_id}/stdout.txt",
            stdout,
        )
        stderr_artifact = self._artifacts.write_text(
            f"verification/{verification_id}/stderr.txt",
            stderr,
        )
        return {
            "verification_id": verification_id,
            "command": command,
            "passed": False,
            "exit_code": None,
            "summary": summary,
            "stdout_artifact": stdout_artifact.path,
            "stderr_artifact": stderr_artifact.path,
        }

    def _timeout_output(self, output: str | bytes | None) -> str:
        if output is None:
            return ""
        if isinstance(output, bytes):
            return output.decode("utf-8", errors="replace")
        return output

    def _format_timeout(self) -> str:
        return f"{self._timeout_seconds:g}"

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
