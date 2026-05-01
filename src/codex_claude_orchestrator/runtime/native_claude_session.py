from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from uuid import uuid4


TmuxRunner = Callable[..., CompletedProcess[str]]


class NativeClaudeSession:
    def __init__(
        self,
        *,
        tmux: str | None = None,
        runner: TmuxRunner | None = None,
        terminal_runner: TmuxRunner | None = None,
        session_name_factory: Callable[[str], str] | None = None,
        turn_marker: str = "<<<CODEX_TURN_DONE status=ready_for_codex>>>",
        open_terminal_on_start: bool = False,
    ):
        self._tmux = tmux or shutil.which("tmux") or "tmux"
        self._runner = runner or subprocess.run
        self._terminal_runner = terminal_runner or subprocess.run
        self._session_name_factory = session_name_factory or (lambda worker_id: f"crew-{worker_id}-{uuid4().hex[:8]}")
        self._turn_marker = turn_marker
        self._open_terminal_on_start = open_terminal_on_start

    def start(
        self,
        *,
        repo_root: Path,
        worker_id: str,
        role: str,
        instructions: str,
        transcript_path: Path,
    ) -> dict[str, str]:
        repo_root = repo_root.resolve()
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        session = self._session_name_factory(worker_id)
        pane = f"{session}:claude.0"
        self._tmux_run(["new-session", "-d", "-s", session, "-c", str(repo_root), "-n", "claude"])
        command = shlex.join(["script", "-q", str(transcript_path), "claude", self._initial_prompt(repo_root, role, instructions)])
        self._tmux_run(["send-keys", "-t", pane, command, "C-m"])
        if self._open_terminal_on_start:
            self._open_terminal(session)
        return {
            "native_session_id": session,
            "terminal_session": session,
            "terminal_pane": pane,
            "transcript_artifact": str(transcript_path),
            "turn_marker": self._turn_marker,
        }

    def send(self, *, terminal_pane: str, message: str, turn_marker: str | None = None) -> dict:
        marker = turn_marker or self._turn_marker
        full_message = (
            f"{message}\n\n"
            f"When this turn is complete, print exactly: {marker}\n"
            "This turn marker overrides any earlier completion marker."
        )
        self._tmux_run(["send-keys", "-t", terminal_pane, full_message, "C-m"])
        return {"message": full_message, "marker": marker}

    def observe(self, *, terminal_pane: str, lines: int = 200, turn_marker: str | None = None) -> dict:
        result = self._tmux_run(["capture-pane", "-p", "-t", terminal_pane, "-S", f"-{lines}"])
        snapshot = result.stdout
        marker = turn_marker or self._turn_marker
        return {"snapshot": snapshot, "marker_seen": marker in snapshot, "marker": marker}

    def tail(self, *, transcript_path: Path, limit: int = 80) -> dict:
        if transcript_path.exists():
            lines = transcript_path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        else:
            lines = []
        return {"transcript_artifact": str(transcript_path), "lines": lines}

    def status(self, *, terminal_session: str) -> dict:
        result = self._tmux_run(["has-session", "-t", terminal_session], check=False)
        return {"running": result.returncode == 0, "terminal_session": terminal_session}

    def stop(self, *, terminal_session: str) -> dict:
        result = self._tmux_run(["kill-session", "-t", terminal_session], check=False)
        return {"terminal_session": terminal_session, "stopped": result.returncode == 0}

    def list_sessions(self) -> list[str]:
        result = self._tmux_run(["list-sessions", "-F", "#{session_name}"], check=False)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def prune_orphans(self, *, active_sessions: set[str], prefix: str = "crew-worker-") -> dict:
        pruned_sessions = []
        for session in self.list_sessions():
            if session.startswith(prefix) and session not in active_sessions:
                result = self._tmux_run(["kill-session", "-t", session], check=False)
                if result.returncode == 0:
                    pruned_sessions.append(session)
        return {"active_sessions": sorted(active_sessions), "pruned_sessions": pruned_sessions}

    def attach(self, *, terminal_session: str) -> dict:
        return {"attach_command": f"{self._tmux} attach -t {terminal_session}"}

    def _open_terminal(self, terminal_session: str) -> None:
        shell_command = shlex.join([self._tmux, "attach", "-t", terminal_session])
        command = [
            "osascript",
            "-e",
            'tell application "Terminal"',
            "-e",
            "activate",
            "-e",
            f"do script {json.dumps(shell_command, ensure_ascii=False)}",
            "-e",
            "end tell",
        ]
        result = self._terminal_runner(command, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)

    def _initial_prompt(self, repo_root: Path, role: str, instructions: str) -> str:
        return "\n".join(
            [
                f"You are a Codex-managed Claude Code worker with role: {role}.",
                f"Workspace: {repo_root}",
                instructions,
                "Report what changed, evidence, risks, and next suggested Codex action.",
                f"When this turn is complete, print exactly: {self._turn_marker}",
            ]
        )

    def _tmux_run(self, args: list[str], *, check: bool = True) -> CompletedProcess[str]:
        result = self._runner(
            [self._tmux, *args],
            text=True,
            capture_output=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
        return result
