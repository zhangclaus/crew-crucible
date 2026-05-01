from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

from codex_claude_orchestrator.runtime.claude_window import ClaudeWindowLauncher


def test_claude_window_launcher_creates_prompt_script_and_launches_terminal(tmp_path: Path):
    repo_root = tmp_path / "研究生" / "论文" / "repo"
    repo_root.mkdir(parents=True)
    calls: list[list[str]] = []

    def fake_runner(command, **kwargs):
        calls.append(list(command))
        return CompletedProcess(command, 0, stdout="", stderr="")

    launcher = ClaudeWindowLauncher(runner=fake_runner, run_id_factory=lambda: "open-fixed")

    result = launcher.open(
        repo_root=repo_root,
        goal="检查项目结构，不要修改文件",
        workspace_mode="readonly",
        terminal_app="terminal",
    )

    assert result.run_id == "open-fixed"
    assert result.repo == repo_root.resolve()
    assert result.prompt_path.is_file()
    assert result.script_path.is_file()
    assert result.transcript_path.parent == result.prompt_path.parent
    assert result.launched is True
    prompt = result.prompt_path.read_text(encoding="utf-8")
    script = result.script_path.read_text(encoding="utf-8")
    assert "检查项目结构，不要修改文件" in prompt
    assert "Do not modify files" in prompt
    assert "pbcopy" in script
    assert "PROMPT_CONTENT" in script
    assert "claude" in script
    assert 'claude "$PROMPT_CONTENT"' in script
    assert "script" in script
    assert calls[0][:2] == ["osascript", "-e"]
    assert "tell application \"Terminal\"" in calls[0][2]
    assert "activate" in calls[0]
    assert any(part.startswith("do script") for part in calls[0])
    do_script = next(part for part in calls[0] if part.startswith("do script"))
    assert "研究生" in do_script
    assert "\\u" not in do_script


def test_claude_window_launcher_dry_run_does_not_launch_terminal(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls: list[list[str]] = []

    def fake_runner(command, **kwargs):
        calls.append(list(command))
        return CompletedProcess(command, 0, stdout="", stderr="")

    launcher = ClaudeWindowLauncher(runner=fake_runner, run_id_factory=lambda: "open-dry")

    result = launcher.open(
        repo_root=repo_root,
        goal="Summarize project",
        workspace_mode="shared",
        terminal_app="terminal",
        dry_run=True,
    )

    assert result.launched is False
    assert calls == []
    assert result.prompt_path.is_file()
    assert result.script_path.is_file()


def test_claude_window_launcher_rejects_missing_repo(tmp_path: Path):
    launcher = ClaudeWindowLauncher(runner=lambda command, **kwargs: CompletedProcess(command, 0))

    try:
        launcher.open(
            repo_root=tmp_path / "missing",
            goal="Inspect",
            workspace_mode="readonly",
            terminal_app="terminal",
        )
    except FileNotFoundError as exc:
        assert "repo not found" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")
