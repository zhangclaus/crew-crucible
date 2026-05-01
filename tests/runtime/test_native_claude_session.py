from pathlib import Path
from subprocess import CompletedProcess

from codex_claude_orchestrator.runtime.native_claude_session import NativeClaudeSession


class FakeTmuxRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[:3] == ["tmux", "capture-pane", "-p"]:
            return CompletedProcess(
                command,
                0,
                stdout="Claude is editing\n<<<CODEX_TURN_DONE status=ready_for_codex>>>\n",
                stderr="",
            )
        if command[:3] == ["tmux", "has-session", "-t"]:
            return CompletedProcess(command, 0, stdout="", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")


def test_native_session_starts_claude_in_tmux_with_transcript_and_marker(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    transcript = tmp_path / "transcript.txt"
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(
        tmux="tmux",
        runner=runner,
        session_name_factory=lambda worker_id: f"crew-1-{worker_id}",
        turn_marker="<<<CODEX_TURN_DONE status=ready_for_codex>>>",
    )

    started = session.start(
        repo_root=repo_root,
        worker_id="worker-explorer",
        role="explorer",
        instructions="Read only. Report facts and risks.",
        transcript_path=transcript,
    )
    sent = session.send(
        terminal_pane=started["terminal_pane"],
        message="Continue the investigation.",
    )
    observed = session.observe(terminal_pane=started["terminal_pane"], lines=200)
    tailed = session.tail(transcript_path=transcript, limit=20)
    status = session.status(terminal_session=started["terminal_session"])
    attached = session.attach(terminal_session=started["terminal_session"])

    commands = [call[0] for call in runner.calls]
    start_command = next(command for command in commands if command[:4] == ["tmux", "send-keys", "-t", "crew-1-worker-explorer:claude.0"])
    assert started == {
        "native_session_id": "crew-1-worker-explorer",
        "terminal_session": "crew-1-worker-explorer",
        "terminal_pane": "crew-1-worker-explorer:claude.0",
        "transcript_artifact": str(transcript),
        "turn_marker": "<<<CODEX_TURN_DONE status=ready_for_codex>>>",
    }
    assert any(command[:4] == ["tmux", "new-session", "-d", "-s"] for command in commands)
    assert any(command[:4] == ["tmux", "send-keys", "-t", "crew-1-worker-explorer:claude.0"] for command in commands)
    assert "claude" in start_command[4]
    assert "Read only. Report facts and risks." in start_command[4]
    assert "Continue the investigation." in sent["message"]
    assert "<<<CODEX_TURN_DONE" in sent["message"]
    assert observed["marker_seen"] is True
    assert tailed["transcript_artifact"] == str(transcript)
    assert status["running"] is True
    assert attached["attach_command"] == "tmux attach -t crew-1-worker-explorer"


def test_native_session_can_open_terminal_attached_to_tmux_session(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    runner = FakeTmuxRunner()
    terminal_calls = []
    session = NativeClaudeSession(
        tmux="tmux",
        runner=runner,
        terminal_runner=lambda command, **kwargs: terminal_calls.append((command, kwargs)) or CompletedProcess(command, 0),
        session_name_factory=lambda worker_id: f"crew-1-{worker_id}",
        open_terminal_on_start=True,
    )

    session.start(
        repo_root=repo_root,
        worker_id="worker-implementer",
        role="implementer",
        instructions="Implement safely.",
        transcript_path=tmp_path / "transcript.txt",
    )

    assert terminal_calls
    assert any("tmux attach -t crew-1-worker-implementer" in part for part in terminal_calls[0][0])


def test_native_session_can_stop_and_prune_crew_tmux_sessions():
    runner = FakeTmuxRunner()

    def fake_runner(command, **kwargs):
        runner.calls.append((command, kwargs))
        if command[:2] == ["tmux", "list-sessions"]:
            return CompletedProcess(
                command,
                0,
                stdout="crew-worker-active\ncrew-worker-orphan\nunrelated\n",
                stderr="",
            )
        return CompletedProcess(command, 0, stdout="", stderr="")

    session = NativeClaudeSession(tmux="tmux", runner=fake_runner)

    stopped = session.stop(terminal_session="crew-worker-active")
    pruned = session.prune_orphans(active_sessions={"crew-worker-active"})

    commands = [call[0] for call in runner.calls]
    assert stopped == {"terminal_session": "crew-worker-active", "stopped": True}
    assert pruned["pruned_sessions"] == ["crew-worker-orphan"]
    assert ["tmux", "kill-session", "-t", "crew-worker-active"] in commands
    assert ["tmux", "kill-session", "-t", "crew-worker-orphan"] in commands


def test_native_session_send_and_observe_can_use_turn_specific_marker():
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(tmux="tmux", runner=runner)

    sent = session.send(
        terminal_pane="crew-worker:claude.0",
        message="Do the next turn.",
        turn_marker="<<<CODEX_TURN_DONE turn=abc>>>",
    )
    observed = session.observe(
        terminal_pane="crew-worker:claude.0",
        lines=80,
        turn_marker="<<<CODEX_TURN_DONE turn=abc>>>",
    )

    assert sent["marker"] == "<<<CODEX_TURN_DONE turn=abc>>>"
    assert "<<<CODEX_TURN_DONE turn=abc>>>" in sent["message"]
    assert "overrides any earlier completion marker" in sent["message"]
    assert observed["marker_seen"] is False
