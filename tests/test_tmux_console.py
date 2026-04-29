from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

from codex_claude_orchestrator.tmux_console import TmuxCommandRunner, TmuxConsole, build_default_term_name


def test_build_default_term_name_uses_safe_repo_slug(tmp_path: Path):
    repo_root = tmp_path / "My Repo!"
    repo_root.mkdir()

    name = build_default_term_name(repo_root, suffix="abc123")

    assert name == "orchestrator-my-repo-abc123"


def test_tmux_console_launches_session_layout_and_internal_runner(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls: list[list[str]] = []

    def fake_runner(command, **kwargs):
        calls.append(list(command))
        return CompletedProcess(command, 0, stdout="", stderr="")

    console = TmuxConsole(tmux="tmux", runner=fake_runner)

    result = console.launch_session_start(
        name="orchestrator-test",
        repo_root=repo_root,
        orchestrator_executable="/tmp/orchestrator",
        session_args=[
            "--goal",
            "Inspect repo",
            "--repo",
            str(repo_root),
            "--workspace-mode",
            "readonly",
        ],
    )

    assert result["tmux_session"] == "orchestrator-test"
    assert result["attach_command"] == "tmux attach -t orchestrator-test"
    assert calls[0] == ["tmux", "new-session", "-d", "-s", "orchestrator-test", "-c", str(repo_root), "-n", "control"]
    assert ["tmux", "new-window", "-t", "orchestrator-test", "-n", "claude", "-c", str(repo_root)] in calls
    assert ["tmux", "new-window", "-t", "orchestrator-test", "-n", "verify", "-c", str(repo_root)] in calls
    assert ["tmux", "new-window", "-t", "orchestrator-test", "-n", "records", "-c", str(repo_root)] in calls
    assert ["tmux", "new-window", "-t", "orchestrator-test", "-n", "skills", "-c", str(repo_root)] in calls
    control_send = next(call for call in calls if call[:4] == ["tmux", "send-keys", "-t", "orchestrator-test:control.0"])
    assert "/tmp/orchestrator term run-session --tmux-name orchestrator-test" in control_send[4]
    assert "--goal 'Inspect repo'" in control_send[4]


def test_tmux_command_runner_returns_completed_process_from_pane_files(tmp_path: Path):
    log_root = tmp_path / "logs"
    calls: list[list[str]] = []

    def fake_runner(command, **kwargs):
        calls.append(list(command))
        if command[:4] == ["tmux", "send-keys", "-t", "orchestrator-test:claude.0"]:
            run_dir = log_root / "cmd-fixed"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "stdout.txt").write_text('{"summary":"ok"}\n', encoding="utf-8")
            (run_dir / "stderr.txt").write_text("warning\n", encoding="utf-8")
            (run_dir / "exit_code.txt").write_text("7", encoding="utf-8")
        return CompletedProcess(command, 0, stdout="", stderr="")

    runner = TmuxCommandRunner(
        target_pane="orchestrator-test:claude.0",
        log_root=log_root,
        tmux="tmux",
        runner=fake_runner,
        command_id_factory=lambda: "cmd-fixed",
        poll_interval_seconds=0,
        timeout_seconds=1,
    )

    completed = runner(["claude", "--print", "hello"], cwd=tmp_path, text=True, capture_output=True, check=False)

    assert completed.args == ["claude", "--print", "hello"]
    assert completed.returncode == 7
    assert completed.stdout == '{"summary":"ok"}\n'
    assert completed.stderr == "warning\n"
    script = (log_root / "cmd-fixed" / "run.zsh").read_text(encoding="utf-8")
    assert "claude --print hello" in script
    assert "tee" in script
    assert calls[-1][:4] == ["tmux", "send-keys", "-t", "orchestrator-test:claude.0"]
