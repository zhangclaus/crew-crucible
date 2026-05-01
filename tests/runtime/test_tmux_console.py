from __future__ import annotations

from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
import subprocess

import pytest

from codex_claude_orchestrator.runtime.tmux_console import TmuxCommandRunner, TmuxConsole, build_default_term_name


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
        if command[:3] == ["tmux", "has-session", "-t"]:
            return CompletedProcess(command, 1, stdout="", stderr="can't find session")
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
    assert calls[0] == ["tmux", "has-session", "-t", "orchestrator-test"]
    assert calls[1] == ["tmux", "new-session", "-d", "-s", "orchestrator-test", "-c", str(repo_root), "-n", "control"]
    assert ["tmux", "new-window", "-t", "orchestrator-test", "-n", "claude", "-c", str(repo_root)] in calls
    assert ["tmux", "new-window", "-t", "orchestrator-test", "-n", "verify", "-c", str(repo_root)] in calls
    assert ["tmux", "new-window", "-t", "orchestrator-test", "-n", "records", "-c", str(repo_root)] in calls
    assert ["tmux", "new-window", "-t", "orchestrator-test", "-n", "skills", "-c", str(repo_root)] in calls
    control_send = next(call for call in calls if call[:4] == ["tmux", "send-keys", "-t", "orchestrator-test:control.0"])
    assert "/tmp/orchestrator term run-session --tmux-name orchestrator-test" in control_send[4]
    assert "--goal 'Inspect repo'" in control_send[4]


def test_tmux_console_rejects_existing_session_name(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    def fake_runner(command, **kwargs):
        return CompletedProcess(command, 0, stdout="", stderr="")

    console = TmuxConsole(tmux="tmux", runner=fake_runner)

    with pytest.raises(FileExistsError, match="tmux session already exists"):
        console.launch_session_start(
            name="orchestrator-test",
            repo_root=repo_root,
            orchestrator_executable="/tmp/orchestrator",
            session_args=["--goal", "Inspect repo", "--repo", str(repo_root)],
        )


def test_tmux_console_raises_when_tmux_command_fails(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    def fake_runner(command, **kwargs):
        if command[:3] == ["tmux", "has-session", "-t"]:
            return CompletedProcess(command, 1, stdout="", stderr="can't find session")
        return CompletedProcess(command, 1, stdout="", stderr="tmux failed")

    console = TmuxConsole(tmux="tmux", runner=fake_runner)

    with pytest.raises(CalledProcessError, match="returned non-zero exit status 1"):
        console.launch_session_start(
            name="orchestrator-test",
            repo_root=repo_root,
            orchestrator_executable="/tmp/orchestrator",
            session_args=["--goal", "Inspect repo", "--repo", str(repo_root)],
        )


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
    assert '"${cmd[@]}"' in script
    assert "tee" in script
    assert calls[-1][:4] == ["tmux", "send-keys", "-t", "orchestrator-test:claude.0"]


def test_tmux_command_runner_script_preserves_multiline_arguments(tmp_path: Path):
    log_root = tmp_path / "logs"

    def fake_runner(command, **kwargs):
        script_command = command[4]
        script_path = script_command.split(" ", 1)[1]
        subprocess.run(["/bin/zsh", script_path], text=True, capture_output=True, check=False)
        return CompletedProcess(command, 0, stdout="", stderr="")

    runner = TmuxCommandRunner(
        target_pane="orchestrator-test:claude.0",
        log_root=log_root,
        tmux="tmux",
        runner=fake_runner,
        command_id_factory=lambda: "cmd-multiline",
        poll_interval_seconds=0,
        timeout_seconds=1,
        heartbeat_interval_seconds=0.01,
    )

    completed = runner(
        [
            "/bin/zsh",
            "-c",
            "printf '%s' \"$1\"",
            "ignored-script-name",
            "line one\nline two",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == "line one\nline two"
    assert completed.stderr == ""
    script = (log_root / "cmd-multiline" / "run.zsh").read_text(encoding="utf-8")
    assert "exit_code=$?" in script
    assert "status=$?" not in script


def test_tmux_command_runner_script_writes_exit_code_after_tee_finishes(tmp_path: Path):
    log_root = tmp_path / "logs"

    def fake_runner(command, **kwargs):
        return CompletedProcess(command, 0, stdout="", stderr="")

    runner = TmuxCommandRunner(
        target_pane="orchestrator-test:claude.0",
        log_root=log_root,
        tmux="tmux",
        runner=fake_runner,
        command_id_factory=lambda: "cmd-pipe-order",
        poll_interval_seconds=0,
        timeout_seconds=1,
    )

    with pytest.raises(subprocess.TimeoutExpired):
        runner(["/bin/zsh", "-c", "printf done"], cwd=tmp_path, text=True, capture_output=True, check=False)

    script = (log_root / "cmd-pipe-order" / "run.zsh").read_text(encoding="utf-8")
    assert "mkfifo" in script
    assert "stdout_tee_pid=$!" in script
    assert "stderr_tee_pid=$!" in script
    assert script.index('wait "$stdout_tee_pid"') < script.index("printf '%s' \"$exit_code\"")
    assert script.index('wait "$stderr_tee_pid"') < script.index("printf '%s' \"$exit_code\"")


def test_tmux_command_runner_script_prints_terminal_heartbeat_without_polluting_stdout(tmp_path: Path):
    log_root = tmp_path / "logs"

    def fake_runner(command, **kwargs):
        script_command = command[4]
        script_path = script_command.split(" ", 1)[1]
        subprocess.run(["/bin/zsh", script_path], text=True, capture_output=True, check=False)
        return CompletedProcess(command, 0, stdout="", stderr="")

    runner = TmuxCommandRunner(
        target_pane="orchestrator-test:claude.0",
        log_root=log_root,
        tmux="tmux",
        runner=fake_runner,
        command_id_factory=lambda: "cmd-heartbeat",
        poll_interval_seconds=0,
        timeout_seconds=1,
        heartbeat_interval_seconds=0.01,
    )

    completed = runner(
        ["/bin/zsh", "-c", "printf payload"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    script = (log_root / "cmd-heartbeat" / "run.zsh").read_text(encoding="utf-8")
    assert "heartbeat_pid=$!" in script
    assert "[orchestrator] still running" in script
    assert 'kill "$heartbeat_pid"' in script
    assert completed.stdout == "payload"
