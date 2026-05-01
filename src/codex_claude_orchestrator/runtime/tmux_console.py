from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess, TimeoutExpired
from typing import Callable
from uuid import uuid4


TmuxRunner = Callable[..., CompletedProcess[str]]


def build_default_term_name(repo_root: Path, suffix: str | None = None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", repo_root.name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug) or "repo"
    return f"orchestrator-{slug}-{suffix or uuid4().hex[:8]}"


class TmuxConsole:
    def __init__(self, tmux: str | None = None, runner: TmuxRunner | None = None):
        self._tmux = tmux or shutil.which("tmux") or "tmux"
        self._runner = runner or subprocess.run

    def launch_session_start(
        self,
        *,
        name: str,
        repo_root: Path,
        orchestrator_executable: str,
        session_args: list[str],
    ) -> dict[str, str]:
        repo_root = repo_root.resolve()
        if self._session_exists(name):
            raise FileExistsError(f"tmux session already exists: {name}")
        self._tmux_run(["new-session", "-d", "-s", name, "-c", str(repo_root), "-n", "control"])
        for window in ("claude", "verify", "records", "skills"):
            self._tmux_run(["new-window", "-t", name, "-n", window, "-c", str(repo_root)])

        self._send_keys(f"{name}:records.0", self._records_loop(orchestrator_executable, repo_root))
        self._send_keys(f"{name}:skills.0", self._skills_loop(orchestrator_executable, repo_root))
        self._send_keys(
            f"{name}:control.0",
            self._run_session_command(orchestrator_executable, name, session_args),
        )
        self._tmux_run(["select-window", "-t", f"{name}:control"])
        return {
            "tmux_session": name,
            "attach_command": f"{self._tmux} attach -t {name}",
        }

    def attach(self, name: str) -> CompletedProcess[str]:
        return self._tmux_run(["attach", "-t", name], capture_output=False)

    def list_sessions(self) -> list[str]:
        result = self._tmux_run(["list-sessions", "-F", "#{session_name}"], check=False)
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    def _session_exists(self, name: str) -> bool:
        result = self._tmux_run(["has-session", "-t", name], check=False)
        return result.returncode == 0

    def _run_session_command(self, executable: str, name: str, session_args: list[str]) -> str:
        command = [executable, "term", "run-session", "--tmux-name", name, *session_args]
        return shlex.join(command)

    def _records_loop(self, executable: str, repo_root: Path) -> str:
        repo = str(repo_root)
        sessions = shlex.join([executable, "sessions", "list", "--repo", repo])
        runs = shlex.join([executable, "runs", "list", "--repo", repo])
        return (
            "while true; do clear; "
            "printf 'Sessions\\n'; "
            f"{sessions}; "
            "printf '\\nRuns\\n'; "
            f"{runs}; "
            "sleep 2; done"
        )

    def _skills_loop(self, executable: str, repo_root: Path) -> str:
        command = shlex.join([executable, "skills", "list", "--repo", str(repo_root), "--status", "pending"])
        return f"while true; do clear; printf 'Pending Skills\\n'; {command}; sleep 2; done"

    def _send_keys(self, target: str, command: str) -> CompletedProcess[str]:
        return self._tmux_run(["send-keys", "-t", target, command, "C-m"])

    def _tmux_run(
        self,
        args: list[str],
        *,
        capture_output: bool = True,
        check: bool = True,
    ) -> CompletedProcess[str]:
        result = self._runner(
            [self._tmux, *args],
            text=True,
            capture_output=capture_output,
            check=False,
        )
        if check and result.returncode != 0:
            raise CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
        return result


class TmuxCommandRunner:
    def __init__(
        self,
        *,
        target_pane: str,
        log_root: Path,
        tmux: str | None = None,
        runner: TmuxRunner | None = None,
        command_id_factory: Callable[[], str] | None = None,
        poll_interval_seconds: float = 0.1,
        timeout_seconds: float = 600,
        heartbeat_interval_seconds: float = 10,
    ):
        self._target_pane = target_pane
        self._log_root = log_root
        self._tmux = tmux or shutil.which("tmux") or "tmux"
        self._runner = runner or subprocess.run
        self._command_id_factory = command_id_factory or (lambda: f"cmd-{uuid4().hex}")
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    def __call__(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        text: bool = True,
        capture_output: bool = True,
        check: bool = False,
        timeout: float | None = None,
        **kwargs,
    ) -> CompletedProcess[str]:
        if not text or not capture_output:
            raise ValueError("TmuxCommandRunner requires text=True and capture_output=True")

        command_id = self._command_id_factory()
        run_dir = self._log_root / command_id
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = run_dir / "stdout.txt"
        stderr_path = run_dir / "stderr.txt"
        exit_path = run_dir / "exit_code.txt"
        script_path = run_dir / "run.zsh"
        script_path.write_text(
            self._render_script(
                args=args,
                cwd=Path(cwd).resolve() if cwd is not None else Path.cwd(),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                exit_path=exit_path,
            ),
            encoding="utf-8",
        )
        script_path.chmod(0o700)

        send_result = self._runner(
            [self._tmux, "send-keys", "-t", self._target_pane, shlex.join(["/bin/zsh", str(script_path)]), "C-m"],
            text=True,
            capture_output=True,
            check=False,
        )
        if send_result.returncode != 0:
            raise CalledProcessError(
                send_result.returncode,
                send_result.args,
                output=send_result.stdout,
                stderr=send_result.stderr,
            )
        return self._wait_for_completion(
            args=args,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            exit_path=exit_path,
            timeout_seconds=timeout or self._timeout_seconds,
            check=check,
        )

    def _render_script(
        self,
        *,
        args: list[str],
        cwd: Path,
        stdout_path: Path,
        stderr_path: Path,
        exit_path: Path,
    ) -> str:
        command_array = ["cmd=("]
        command_array.extend(f"  {shlex.quote(str(arg))}" for arg in args)
        command_array.append(")")
        stdout_pipe = stdout_path.with_name("stdout.pipe")
        stderr_pipe = stderr_path.with_name("stderr.pipe")
        return "\n".join(
            [
                "#!/bin/zsh",
                "set +e",
                f"cd {shlex.quote(str(cwd))}",
                *command_array,
                "printf '\\n[orchestrator] $'",
                "printf ' %q' \"${cmd[@]}\"",
                "printf '\\n'",
                f"stdout_pipe={shlex.quote(str(stdout_pipe))}",
                f"stderr_pipe={shlex.quote(str(stderr_pipe))}",
                'rm -f "$stdout_pipe" "$stderr_pipe"',
                'mkfifo "$stdout_pipe" "$stderr_pipe"',
                f"tee {shlex.quote(str(stdout_path))} < \"$stdout_pipe\" &",
                "stdout_tee_pid=$!",
                f"tee {shlex.quote(str(stderr_path))} < \"$stderr_pipe\" >&2 &",
                "stderr_tee_pid=$!",
                "heartbeat_seconds=0",
                "(",
                "  while true; do",
                f"    sleep {self._heartbeat_interval_seconds:g}",
                f"    heartbeat_seconds=$((heartbeat_seconds + {int(self._heartbeat_interval_seconds)}))",
                (
                    "    printf '\\n[orchestrator] still running (%ss): %s\\n' "
                    '"$heartbeat_seconds" "${cmd[1]:-${cmd[0]}}" > /dev/tty 2>/dev/null || true'
                ),
                "  done",
                ") &",
                "heartbeat_pid=$!",
                '"${cmd[@]}" > "$stdout_pipe" 2> "$stderr_pipe"',
                "exit_code=$?",
                'kill "$heartbeat_pid" >/dev/null 2>&1 || true',
                'wait "$heartbeat_pid" >/dev/null 2>&1 || true',
                'wait "$stdout_tee_pid"',
                'wait "$stderr_tee_pid"',
                'rm -f "$stdout_pipe" "$stderr_pipe"',
                f"printf '%s' \"$exit_code\" > {shlex.quote(str(exit_path))}",
                "printf '\\n[orchestrator] exit %s\\n' \"$exit_code\"",
                "exit \"$exit_code\"",
                "",
            ]
        )

    def _wait_for_completion(
        self,
        *,
        args: list[str],
        stdout_path: Path,
        stderr_path: Path,
        exit_path: Path,
        timeout_seconds: float,
        check: bool,
    ) -> CompletedProcess[str]:
        deadline = time.monotonic() + timeout_seconds
        while not exit_path.exists():
            if time.monotonic() >= deadline:
                raise TimeoutExpired(args, timeout_seconds)
            time.sleep(self._poll_interval_seconds)

        stdout = stdout_path.read_text(encoding="utf-8") if stdout_path.exists() else ""
        stderr = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
        returncode = int(exit_path.read_text(encoding="utf-8").strip() or "0")
        completed = CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)
        if check and returncode != 0:
            raise CalledProcessError(returncode, args, output=stdout, stderr=stderr)
        return completed
