import json
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
                stdout="Claude is editing\n<<<WORKER_TURN_DONE>>>\n",
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
        turn_marker="<<<WORKER_TURN_DONE>>>",
    )

    started = session.start(
        repo_root=repo_root,
        worker_id="worker-explorer",
        role="explorer",
        instructions="Read only. Report facts and risks.",
        transcript_path=transcript,
    )
    # send with work_dir to use new inbox/outbox protocol
    sent = session.send(
        terminal_pane=started["terminal_pane"],
        message="Continue the investigation.",
        work_dir=Path(started["work_dir"]),
    )
    # observe with work_dir falls back to tmux pane capture when no result.json
    observed = session.observe(terminal_pane=started["terminal_pane"], lines=200)
    tailed = session.tail(transcript_path=transcript, limit=20)
    status = session.status(terminal_session=started["terminal_session"])
    attached = session.attach(terminal_session=started["terminal_session"])

    commands = [call[0] for call in runner.calls]
    start_command = next(command for command in commands if command[:4] == ["tmux", "send-keys", "-t", "crew-1-worker-explorer:claude.0"])
    assert started["native_session_id"] == "crew-1-worker-explorer"
    assert started["terminal_session"] == "crew-1-worker-explorer"
    assert started["terminal_pane"] == "crew-1-worker-explorer:claude.0"
    assert started["transcript_artifact"] == str(transcript)
    assert started["turn_marker"] == "<<<WORKER_TURN_DONE>>>"
    assert "work_dir" in started
    assert any(command[:4] == ["tmux", "new-session", "-d", "-s"] for command in commands)
    assert any(command[:4] == ["tmux", "send-keys", "-t", "crew-1-worker-explorer:claude.0"] for command in commands)
    # start() now launches claude_worker.sh instead of inline claude
    assert "claude_worker.sh" in start_command[4]
    assert "Continue the investigation." in sent["message"]
    assert observed["marker_seen"] is True
    assert tailed["transcript_artifact"] == str(transcript)
    assert status["running"] is True
    assert attached["attach_command"] == "tmux attach -t crew-1-worker-explorer"


def test_native_session_can_open_terminal_attached_to_tmux_session(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    runner = FakeTmuxRunner()
    popen_calls = []
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda command, **kwargs: popen_calls.append(command) or None,
    )
    session = NativeClaudeSession(
        tmux="tmux",
        runner=runner,
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

    assert popen_calls
    assert any("tmux attach -t crew-1-worker-implementer" in part for part in popen_calls[0])


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
        turn_marker="<<<WORKER_TURN_DONE turn=abc>>>",
    )
    observed = session.observe(
        terminal_pane="crew-worker:claude.0",
        lines=80,
        turn_marker="<<<WORKER_TURN_DONE turn=abc>>>",
    )

    assert sent["marker"] == "<<<WORKER_TURN_DONE turn=abc>>>"
    assert "<<<WORKER_TURN_DONE turn=abc>>>" in sent["message"]
    assert "overrides any earlier completion marker" in sent["message"]
    assert observed["marker_seen"] is False


# --- New tests for inbox/outbox wrapper integration ---


def test_start_uses_wrapper_script(tmp_path: Path):
    """start() should create work dirs, write mission.md, and launch claude_worker.sh."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    transcript = tmp_path / "transcript.txt"
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(
        tmux="tmux",
        runner=runner,
        session_name_factory=lambda worker_id: f"crew-1-{worker_id}",
    )

    started = session.start(
        repo_root=repo_root,
        worker_id="worker-explorer",
        role="explorer",
        instructions="Read only. Report facts and risks.",
        transcript_path=transcript,
    )

    # work_dir must be in returned dict
    assert "work_dir" in started
    work_dir = Path(started["work_dir"])
    assert work_dir.exists()

    # subdirectories must exist
    assert (work_dir / ".inbox").is_dir()
    assert (work_dir / ".outbox").is_dir()
    assert (work_dir / ".crew-history").is_dir()

    # mission.md must be written
    mission_path = work_dir / ".inbox" / "mission.md"
    assert mission_path.exists()
    mission_content = mission_path.read_text()
    assert "explorer" in mission_content
    assert "Read only. Report facts and risks." in mission_content

    # tmux send-keys must invoke claude_worker.sh with the work_dir
    commands = [call[0] for call in runner.calls]
    send_keys_cmds = [
        c for c in commands
        if c[:4] == ["tmux", "send-keys", "-t", "crew-1-worker-explorer:claude.0"]
    ]
    assert len(send_keys_cmds) >= 1
    start_cmd = send_keys_cmds[0][4]  # the command string sent to tmux
    assert "claude_worker.sh" in start_cmd
    assert str(work_dir) in start_cmd


def test_send_writes_inbox_files(tmp_path: Path):
    """send() with work_dir should write .inbox/task.md and send tmux trigger."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    transcript = tmp_path / "transcript.txt"
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(
        tmux="tmux",
        runner=runner,
        session_name_factory=lambda worker_id: f"crew-1-{worker_id}",
    )

    started = session.start(
        repo_root=repo_root,
        worker_id="worker-writer",
        role="implementer",
        instructions="Implement features.",
        transcript_path=transcript,
    )

    work_dir = Path(started["work_dir"])
    # Clear mission.md send-keys calls from start()
    runner.calls.clear()

    sent = session.send(
        terminal_pane=started["terminal_pane"],
        message="Fix the bug in auth.py",
        work_dir=work_dir,
    )

    # .inbox/task.md must be written with the message content
    task_path = work_dir / ".inbox" / "task.md"
    assert task_path.exists()
    task_content = task_path.read_text()
    assert "Fix the bug in auth.py" in task_content

    # tmux trigger must be sent (send-keys to the pane)
    commands = [call[0] for call in runner.calls]
    send_keys_cmds = [
        c for c in commands
        if c[:4] == ["tmux", "send-keys", "-t", started["terminal_pane"]]
    ]
    assert len(send_keys_cmds) >= 1

    # returned dict must contain the message
    assert "Fix the bug in auth.py" in sent["message"]


def test_observe_watches_outbox(tmp_path: Path):
    """observe() with work_dir should detect .outbox/result.json presence."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    transcript = tmp_path / "transcript.txt"
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(
        tmux="tmux",
        runner=runner,
        session_name_factory=lambda worker_id: f"crew-1-{worker_id}",
    )

    started = session.start(
        repo_root=repo_root,
        worker_id="worker-reviewer",
        role="reviewer",
        instructions="Review code.",
        transcript_path=transcript,
    )

    work_dir = Path(started["work_dir"])

    # Before result.json exists: marker_seen should be False
    observed_no_result = session.observe(
        terminal_pane=started["terminal_pane"],
        work_dir=work_dir,
    )
    assert observed_no_result["marker_seen"] is False

    # Write result.json
    result_data = {
        "crew_id": "crew-1",
        "worker_id": "worker-reviewer",
        "turn_id": "1",
        "status": "completed",
        "summary": "Reviewed auth module",
        "changed_files": ["auth.py"],
        "verification": "",
        "risks": "none",
        "next_suggested_action": "proceed",
    }
    result_path = work_dir / ".outbox" / "result.json"
    result_path.write_text(json.dumps(result_data))

    # After result.json exists: marker_seen should be True
    observed_with_result = session.observe(
        terminal_pane=started["terminal_pane"],
        work_dir=work_dir,
    )
    assert observed_with_result["marker_seen"] is True


def test_observe_reads_result_content(tmp_path: Path):
    """observe() with work_dir should parse and return the result JSON."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    transcript = tmp_path / "transcript.txt"
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(
        tmux="tmux",
        runner=runner,
        session_name_factory=lambda worker_id: f"crew-1-{worker_id}",
    )

    started = session.start(
        repo_root=repo_root,
        worker_id="worker-explorer",
        role="explorer",
        instructions="Explore.",
        transcript_path=transcript,
    )

    work_dir = Path(started["work_dir"])

    result_data = {
        "crew_id": "crew-1",
        "worker_id": "worker-explorer",
        "turn_id": "2",
        "status": "completed",
        "summary": "Found 3 risks in auth module",
        "changed_files": [],
        "verification": "all tests pass",
        "risks": "SQL injection in login endpoint",
        "next_suggested_action": "fix sql injection",
    }
    result_path = work_dir / ".outbox" / "result.json"
    result_path.write_text(json.dumps(result_data))

    observed = session.observe(
        terminal_pane=started["terminal_pane"],
        work_dir=work_dir,
    )

    assert observed["marker_seen"] is True
    assert observed["result"] == result_data
    assert observed["result"]["summary"] == "Found 3 risks in auth module"
    assert observed["result"]["status"] == "completed"


def test_send_without_work_dir_uses_legacy_behavior():
    """send() without work_dir should fall back to legacy claude -p behavior."""
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(tmux="tmux", runner=runner)

    sent = session.send(
        terminal_pane="crew-worker:claude.0",
        message="Do the next turn.",
        turn_marker="<<<WORKER_TURN_DONE turn=abc>>>",
    )

    # Legacy send uses claude -p with the message
    commands = [call[0] for call in runner.calls]
    send_keys_cmds = [
        c for c in commands
        if c[:4] == ["tmux", "send-keys", "-t", "crew-worker:claude.0"]
    ]
    assert len(send_keys_cmds) >= 1
    assert "claude" in send_keys_cmds[0][4]
    assert sent["marker"] == "<<<WORKER_TURN_DONE turn=abc>>>"
    assert "Do the next turn." in sent["message"]


def test_observe_without_work_dir_uses_legacy_behavior():
    """observe() without work_dir should fall back to tmux pane capture."""
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(tmux="tmux", runner=runner)

    observed = session.observe(
        terminal_pane="crew-worker:claude.0",
        lines=80,
        turn_marker="<<<WORKER_TURN_DONE>>>",
    )

    assert observed["marker_seen"] is True
    assert "snapshot" in observed
    assert "<<<WORKER_TURN_DONE" in observed["snapshot"]
