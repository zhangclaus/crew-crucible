from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from typing import Callable
from uuid import uuid4


CommandRunner = Callable[..., CompletedProcess[str]]


@dataclass
class ClaudeWindowLaunch:
    run_id: str
    repo: Path
    prompt_path: Path
    script_path: Path
    transcript_path: Path
    terminal_app: str
    launched: bool
    open_command: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "repo": str(self.repo),
            "prompt_path": str(self.prompt_path),
            "script_path": str(self.script_path),
            "transcript_path": str(self.transcript_path),
            "terminal_app": self.terminal_app,
            "launched": self.launched,
            "open_command": list(self.open_command),
        }


class ClaudeWindowLauncher:
    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ):
        self._runner = runner or subprocess.run
        self._run_id_factory = run_id_factory or (lambda: f"claude-open-{uuid4().hex}")

    def open(
        self,
        *,
        repo_root: Path,
        goal: str,
        workspace_mode: str = "readonly",
        terminal_app: str = "terminal",
        dry_run: bool = False,
    ) -> ClaudeWindowLaunch:
        repo = repo_root.resolve()
        if not repo.is_dir():
            raise FileNotFoundError(f"repo not found: {repo}")
        if terminal_app != "terminal":
            raise ValueError("only terminal is supported in this version")

        run_id = self._run_id_factory()
        run_dir = repo / ".orchestrator" / "claude-open" / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        prompt_path = run_dir / "prompt.txt"
        script_path = run_dir / "open.zsh"
        transcript_path = run_dir / "transcript.txt"

        prompt_path.write_text(self._render_prompt(repo, goal, workspace_mode), encoding="utf-8")
        script_path.write_text(
            self._render_script(
                repo=repo,
                prompt_path=prompt_path,
                transcript_path=transcript_path,
            ),
            encoding="utf-8",
        )
        script_path.chmod(0o700)

        open_command = self._terminal_open_command(script_path)
        if not dry_run:
            result = self._runner(open_command, text=True, capture_output=True, check=False)
            if result.returncode != 0:
                raise CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)

        return ClaudeWindowLaunch(
            run_id=run_id,
            repo=repo,
            prompt_path=prompt_path,
            script_path=script_path,
            transcript_path=transcript_path,
            terminal_app=terminal_app,
            launched=not dry_run,
            open_command=open_command,
        )

    def _render_prompt(self, repo: Path, goal: str, workspace_mode: str) -> str:
        constraints = [
            "You are Claude Code running in an interactive terminal opened by Codex.",
            f"Repository: {repo}",
            f"Goal: {goal}",
            f"Workspace mode: {workspace_mode}",
        ]
        if workspace_mode == "readonly":
            constraints.append("Do not modify files. Inspect only and report findings.")
        else:
            constraints.append("Preserve unrelated user work and summarize every file you change.")
        constraints.extend(
            [
                "",
                "When you finish, report:",
                "- summary",
                "- files inspected or changed",
                "- verification performed",
                "- open questions or blockers",
            ]
        )
        return "\n".join(constraints) + "\n"

    def _render_script(self, *, repo: Path, prompt_path: Path, transcript_path: Path) -> str:
        return "\n".join(
            [
                "#!/bin/zsh",
                "set -e",
                f"cd {shlex.quote(str(repo))}",
                "clear",
                "printf '\\n[orchestrator] Claude interactive session\\n'",
                f"printf '[orchestrator] repo: %s\\n' {shlex.quote(str(repo))}",
                f"printf '[orchestrator] prompt: %s\\n' {shlex.quote(str(prompt_path))}",
                f"printf '[orchestrator] transcript: %s\\n\\n' {shlex.quote(str(transcript_path))}",
                f"cat {shlex.quote(str(prompt_path))} | pbcopy",
                "PROMPT_CONTENT=\"$(cat " + shlex.quote(str(prompt_path)) + ")\"",
                "printf '[orchestrator] Task prompt copied to clipboard as backup. Launching Claude now.\\n\\n'",
                f"script -q {shlex.quote(str(transcript_path))} claude \"$PROMPT_CONTENT\"",
                "",
            ]
        )

    def _terminal_open_command(self, script_path: Path) -> list[str]:
        shell_command = shlex.join(["/bin/zsh", str(script_path)])
        return [
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
