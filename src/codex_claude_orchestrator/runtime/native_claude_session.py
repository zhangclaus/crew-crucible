from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from uuid import uuid4


TmuxRunner = Callable[..., CompletedProcess[str]]


def _safe_session_name(worker_id: str) -> str:
    """Sanitize worker_id for use as tmux session name."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", worker_id)
    return f"crew-{safe}-{uuid4().hex[:8]}"


def _escape_turn_markers(message: str) -> str:
    """Prevent injection of fake completion markers."""
    return (
        message
        .replace("<<<CODEX_TURN_DONE", "[MARKER_ESCAPED]")
        .replace("<<<WORKER_TURN_DONE", "[MARKER_ESCAPED]")
    )


def _wrapper_script_path() -> Path:
    """Return path to claude_worker.sh wrapper script."""
    return Path(__file__).parent / "claude_worker.sh"


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
        self._session_name_factory = session_name_factory or _safe_session_name
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

        # Create work directory structure for inbox/outbox protocol
        work_dir = repo_root / ".worker" / worker_id
        for subdir in (".inbox", ".outbox", ".crew-history"):
            (work_dir / subdir).mkdir(parents=True, exist_ok=True)

        # Write mission file
        mission_content = self._initial_prompt(repo_root, role, instructions)
        (work_dir / ".inbox" / "mission.md").write_text(mission_content, encoding="utf-8")

        # Create tmux session and launch wrapper script
        self._tmux_run(["new-session", "-d", "-s", session, "-c", str(repo_root), "-n", "claude"])
        wrapper = _wrapper_script_path()
        self._tmux_run(["send-keys", "-t", pane, f"{wrapper} {work_dir}", "C-m"])
        if self._open_terminal_on_start:
            self._open_terminal(session)
        return {
            "native_session_id": session,
            "terminal_session": session,
            "terminal_pane": pane,
            "transcript_artifact": str(transcript_path),
            "turn_marker": self._turn_marker,
            "work_dir": str(work_dir),
        }

    def send(self, *, terminal_pane: str, message: str, turn_marker: str | None = None, work_dir: Path | None = None) -> dict:
        marker = turn_marker or self._turn_marker
        safe_message = _escape_turn_markers(message)

        if work_dir is not None:
            # New protocol: write task to .inbox/task.md, trigger wrapper
            work_dir = Path(work_dir)
            task_path = work_dir / ".inbox" / "task.md"
            task_path.write_text(safe_message, encoding="utf-8")
            # Send a newline to the running claude session to trigger re-read
            trigger = f"cat {work_dir / '.inbox' / 'task.md'}"
            self._tmux_run(["send-keys", "-t", terminal_pane, trigger, "C-m"])
            return {"message": safe_message, "marker": marker}

        # Legacy behavior: use claude -p to skip trust prompts and execute turn
        full_message = (
            f"{safe_message}\n\n"
            f"When this turn is complete, print exactly: {marker}\n"
            "This turn marker overrides any earlier completion marker."
        )
        escaped_message = full_message.replace('"', '\\"').replace('\n', '\\n')
        command = f'bash -c "claude -p \\"{escaped_message}\\""'
        self._tmux_run(["send-keys", "-t", terminal_pane, command, "C-m"])
        return {"message": full_message, "marker": marker}

    def observe(self, *, terminal_pane: str, lines: int = 200, turn_marker: str | None = None, work_dir: Path | None = None) -> dict:
        marker = turn_marker or self._turn_marker

        if work_dir is not None:
            # New protocol: check .outbox/result.json for completion
            work_dir = Path(work_dir)
            result_path = work_dir / ".outbox" / "result.json"
            if result_path.exists():
                try:
                    result_data = json.loads(result_path.read_text(encoding="utf-8"))
                    return {"snapshot": "", "marker_seen": True, "marker": marker, "result": result_data}
                except (json.JSONDecodeError, OSError):
                    pass
            # No result yet — fall back to tmux pane capture for legacy marker detection
            result = self._tmux_run(["capture-pane", "-p", "-t", terminal_pane, "-S", f"-{lines}"])
            snapshot = result.stdout
            return {"snapshot": snapshot, "marker_seen": marker in snapshot, "marker": marker}

        # Legacy behavior: tmux pane capture
        result = self._tmux_run(["capture-pane", "-p", "-t", terminal_pane, "-S", f"-{lines}"])
        snapshot = result.stdout
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
        return {"attach_command": f"{self._tmux} attach -t {shlex.quote(terminal_session)}"}

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
        # Fire-and-forget: don't block worker startup waiting for Terminal.app.
        # Use DEVNULL to avoid hanging on osascript when run from MCP subprocess.
        import subprocess as _sp
        try:
            _sp.Popen(command, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        except Exception:
            pass

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
