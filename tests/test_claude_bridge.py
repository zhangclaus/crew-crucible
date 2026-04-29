from __future__ import annotations

import json
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess

from codex_claude_orchestrator.claude_bridge import ClaudeBridge
from codex_claude_orchestrator.models import SessionStatus, VerificationKind, VerificationRecord
from codex_claude_orchestrator.session_recorder import SessionRecorder


class FakeBridgeVerificationRunner:
    def __init__(self, recorder: SessionRecorder, results: list[bool]):
        self._recorder = recorder
        self._results = list(results)
        self.commands: list[str] = []

    def run(self, session_id: str, turn_id: str, command: str) -> VerificationRecord:
        self.commands.append(command)
        passed = self._results.pop(0)
        record = VerificationRecord(
            verification_id=f"verification-{len(self.commands)}",
            session_id=session_id,
            turn_id=turn_id,
            kind=VerificationKind.COMMAND,
            passed=passed,
            command=command,
            exit_code=0 if passed else 1,
            summary=f"verification {'passed' if passed else 'failed'}",
        )
        self._recorder.append_verification(session_id, record)
        return record


class FakeBlockedBridgeVerificationRunner:
    def __init__(self, recorder: SessionRecorder):
        self._recorder = recorder
        self.commands: list[str] = []

    def run(self, session_id: str, turn_id: str, command: str) -> VerificationRecord:
        self.commands.append(command)
        record = VerificationRecord(
            verification_id=f"verification-{len(self.commands)}",
            session_id=session_id,
            turn_id=turn_id,
            kind=VerificationKind.COMMAND,
            passed=False,
            command=command,
            exit_code=None,
            summary="command blocked: blocked command wrapper: bash -lc",
        )
        self._recorder.append_verification(session_id, record)
        return record


def test_bridge_start_runs_claude_and_records_latest_session(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls = []

    def fake_runner(command, **kwargs):
        calls.append({"command": list(command), "cwd": kwargs["cwd"]})
        return CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"已检查项目结构。"}',
            stderr="",
        )

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        bridge_id_factory=lambda: "bridge-fixed",
        turn_id_factory=lambda: "turn-start",
    )

    result = bridge.start(repo_root=repo_root, goal="检查项目结构，不要修改文件", workspace_mode="readonly")

    assert result["bridge"]["bridge_id"] == "bridge-fixed"
    assert result["bridge"]["claude_session_id"] == "claude-session-1"
    assert result["bridge"]["status"] == "active"
    assert result["latest_turn"]["result_text"] == "已检查项目结构。"
    assert calls[0]["cwd"] == str(repo_root.resolve())
    assert calls[0]["command"][0:2] == ["claude", "--print"]
    assert "--output-format" in calls[0]["command"]
    assert "--allowedTools" in calls[0]["command"]
    assert "Read,Glob,Grep,LS" in calls[0]["command"]

    tail = bridge.tail(repo_root=repo_root, bridge_id=None, limit=5)

    assert tail["bridge"]["bridge_id"] == "bridge-fixed"
    assert tail["turns"][0]["turn_id"] == "turn-start"
    assert (repo_root / ".orchestrator" / "claude-bridge" / "latest").read_text(encoding="utf-8") == "bridge-fixed"


def test_bridge_start_with_log_visual_opens_append_only_window(tmp_path: Path):
    repo_root = tmp_path / "研究生" / "论文" / "repo"
    repo_root.mkdir(parents=True)
    visual_calls = []
    call_order = []

    def fake_runner(command, **kwargs):
        call_order.append("claude")
        return CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"第一轮完成。"}',
            stderr="",
        )

    def fake_visual_runner(command, **kwargs):
        call_order.append("visual")
        visual_calls.append(list(command))
        return CompletedProcess(command, 0, stdout="", stderr="")

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        visual_runner=fake_visual_runner,
        bridge_id_factory=lambda: "bridge-visible",
        turn_id_factory=lambda: "turn-start",
    )

    result = bridge.start(
        repo_root=repo_root,
        goal="检查项目结构",
        workspace_mode="readonly",
        visual="log",
    )

    visual = result["visual"]
    watch_script = Path(visual["watch_script_path"])
    log_path = Path(visual["log_path"])

    assert visual["mode"] == "log"
    assert visual["launched"] is True
    assert call_order == ["visual", "claude"]
    assert watch_script.is_file()
    assert log_path.is_file()
    assert visual_calls[0][:2] == ["osascript", "-e"]
    assert "activate" in visual_calls[0]
    assert any(part.startswith("do script") for part in visual_calls[0])
    do_script = next(part for part in visual_calls[0] if part.startswith("do script"))
    assert "研究生" in do_script
    assert "\\u" not in do_script
    script = watch_script.read_text(encoding="utf-8")
    log_text = log_path.read_text(encoding="utf-8")
    assert "tail -n +1 -f" in script
    assert "while true" not in script
    assert "clear" not in script
    assert "Claude bridge log" in log_text
    assert "第一轮完成。" in log_text


def test_bridge_send_appends_human_readable_log(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    responses = [
        CompletedProcess(
            ["claude"],
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"开始。"}',
            stderr="",
        ),
        CompletedProcess(
            ["claude"],
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"继续完成。"}',
            stderr="",
        ),
    ]

    def fake_runner(command, **kwargs):
        return responses.pop(0)

    turn_ids = iter(["turn-start", "turn-send"])
    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        visual_runner=lambda command, **kwargs: CompletedProcess(command, 0, stdout="", stderr=""),
        bridge_id_factory=lambda: "bridge-log",
        turn_id_factory=lambda: next(turn_ids),
    )

    bridge.start(repo_root=repo_root, goal="检查项目结构", workspace_mode="readonly", visual="log")
    bridge.send(repo_root=repo_root, bridge_id=None, message="继续检查")

    log_path = repo_root / ".orchestrator" / "claude-bridge" / "bridge-log" / "bridge.log"
    log_text = log_path.read_text(encoding="utf-8")

    assert "[USER]" in log_text
    assert "[CLAUDE]" in log_text
    assert "继续检查" in log_text
    assert "继续完成。" in log_text


def test_bridge_start_with_visual_failure_does_not_start_claude(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    claude_calls = []
    recorder = SessionRecorder(repo_root / ".orchestrator")

    def fake_runner(command, **kwargs):
        claude_calls.append(list(command))
        return CompletedProcess(command, 0, stdout="", stderr="")

    def fake_visual_runner(command, **kwargs):
        return CompletedProcess(command, 1, stdout="", stderr="operation not permitted")

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        visual_runner=fake_visual_runner,
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-visual-fail",
        turn_id_factory=lambda: "turn-never",
        session_id_factory=lambda: "session-visual-fail",
    )

    try:
        bridge.start(
            repo_root=repo_root,
            goal="检查项目结构",
            workspace_mode="readonly",
            visual="log",
            supervised=True,
        )
    except CalledProcessError as exc:
        assert "operation not permitted" in str(exc.stderr)
    else:
        raise AssertionError("expected CalledProcessError")

    assert claude_calls == []
    assert recorder.list_sessions() == []


def test_bridge_send_resumes_existing_claude_session(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    responses = [
        CompletedProcess(
            ["claude"],
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"开始。"}',
            stderr="",
        ),
        CompletedProcess(
            ["claude"],
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"继续检查后端接口。"}',
            stderr="",
        ),
    ]
    calls = []

    def fake_runner(command, **kwargs):
        calls.append({"command": list(command), "cwd": kwargs["cwd"]})
        return responses.pop(0)

    turn_ids = iter(["turn-start", "turn-send"])
    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        bridge_id_factory=lambda: "bridge-fixed",
        turn_id_factory=lambda: next(turn_ids),
    )

    bridge.start(repo_root=repo_root, goal="检查项目结构", workspace_mode="readonly")
    result = bridge.send(repo_root=repo_root, bridge_id=None, message="继续检查后端接口")

    assert result["bridge"]["claude_session_id"] == "claude-session-1"
    assert result["latest_turn"]["turn_id"] == "turn-send"
    assert result["latest_turn"]["result_text"] == "继续检查后端接口。"
    assert "--resume" in calls[1]["command"]
    assert "claude-session-1" in calls[1]["command"]
    assert "继续检查后端接口" in calls[1]["command"]

    tail = bridge.tail(repo_root=repo_root, bridge_id="bridge-fixed", limit=1)

    assert len(tail["turns"]) == 1
    assert tail["turns"][0]["turn_id"] == "turn-send"


def test_bridge_send_requires_claude_session_for_real_send(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(command, 0, stdout="", stderr=""),
        bridge_id_factory=lambda: "bridge-dry",
        turn_id_factory=lambda: "turn-dry",
    )
    result = bridge.start(repo_root=repo_root, goal="准备会话", workspace_mode="readonly", dry_run=True)

    assert result["bridge"]["status"] == "created"
    assert result["bridge"]["claude_session_id"] is None

    try:
        bridge.send(repo_root=repo_root, bridge_id=None, message="继续")
    except ValueError as exc:
        assert "no Claude session id" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_bridge_start_supervised_creates_session_and_mirrors_initial_turn(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")

    def fake_runner(command, **kwargs):
        return CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"初始实现完成。"}',
            stderr="",
        )

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-supervised",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-bridge",
        task_id_factory=lambda: "task-bridge",
        trace_id_factory=lambda: "trace-start",
    )

    result = bridge.start(
        repo_root=repo_root,
        goal="实现 Codex 监督 bridge",
        workspace_mode="shared",
        supervised=True,
    )

    assert result["bridge"]["supervised"] is True
    assert result["bridge"]["session_id"] == "session-bridge"
    assert result["bridge"]["latest_turn_id"] == "turn-start"
    assert result["latest_turn"]["result_text"] == "初始实现完成。"

    details = recorder.read_session("session-bridge")
    assert details["session"]["goal"] == "实现 Codex 监督 bridge"
    assert details["session"]["assigned_agent"] == "claude"
    assert details["session"]["workspace_mode"] == "shared"
    assert details["turns"][0]["turn_id"] == "turn-start"
    assert details["turns"][0]["phase"] == "execute"
    assert details["turns"][0]["from_agent"] == "claude"
    assert details["turns"][0]["to_agent"] == "codex"
    assert details["output_traces"][0]["trace_id"] == "trace-start"
    assert details["output_traces"][0]["run_id"] == "turn-start"
    assert details["output_traces"][0]["command"][0:2] == ["claude", "--print"]
    assert details["output_traces"][0]["stdout_artifact"].endswith("bridge/turn-start/stdout.txt")
    assert details["output_traces"][0]["stderr_artifact"].endswith("bridge/turn-start/stderr.txt")
    assert details["output_traces"][0]["evaluation"]["accepted"] is True


def test_bridge_start_supervised_failure_finalizes_linked_session_needs_human(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    calls = []

    def fake_runner(command, **kwargs):
        calls.append(list(command))
        return CompletedProcess(
            command,
            7,
            stdout='{"type":"result","session_id":"claude-session-1","result":"失败。"}',
            stderr="boom",
        )

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-start-fail",
        turn_id_factory=lambda: "turn-start-fail",
        session_id_factory=lambda: "session-start-fail",
        task_id_factory=lambda: "task-start-fail",
        trace_id_factory=lambda: "trace-start-fail",
    )

    result = bridge.start(
        repo_root=repo_root,
        goal="实现功能",
        workspace_mode="shared",
        supervised=True,
    )

    assert result["bridge"]["status"] == "failed"
    details = recorder.read_session("session-start-fail")
    assert details["session"]["status"] == "needs_human"
    assert details["final_report"]["status"] == "needs_human"
    assert (
        "Claude start turn failed" in details["final_report"]["final_summary"]
        or "non-zero" in details["final_report"]["final_summary"]
    )
    assert details["turns"][0]["turn_id"] == "turn-start-fail"
    assert details["output_traces"][0]["turn_id"] == "turn-start-fail"

    try:
        bridge.send(repo_root=repo_root, bridge_id=None, message="继续")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    assert len(calls) == 1


def test_bridge_send_supervised_mirrors_follow_up_turn_in_session_round_one(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    responses = [
        CompletedProcess(
            ["claude"],
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"初始实现完成。"}',
            stderr="",
        ),
        CompletedProcess(
            ["claude"],
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"后续实现完成。"}',
            stderr="warning: note\n",
        ),
    ]

    def fake_runner(command, **kwargs):
        return responses.pop(0)

    turn_ids = iter(["turn-start", "turn-send"])
    trace_ids = iter(["trace-start", "trace-send"])
    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-supervised",
        turn_id_factory=lambda: next(turn_ids),
        session_id_factory=lambda: "session-bridge",
        task_id_factory=lambda: "task-bridge",
        trace_id_factory=lambda: next(trace_ids),
    )

    bridge.start(
        repo_root=repo_root,
        goal="实现 Codex 监督 bridge",
        workspace_mode="shared",
        supervised=True,
    )
    result = bridge.send(
        repo_root=repo_root,
        bridge_id=None,
        message="继续实现",
    )

    assert result["bridge"]["latest_turn_id"] == "turn-send"

    details = recorder.read_session("session-bridge")
    execute_turns = [turn for turn in details["turns"] if turn["phase"] == "execute"]
    assert len(execute_turns) == 2
    assert len(details["output_traces"]) == 2
    assert [turn["round_index"] for turn in execute_turns] == [1, 1]
    assert details["session"]["max_rounds"] == 1
    assert details["output_traces"][1]["trace_id"] == "trace-send"
    assert details["output_traces"][1]["stdout_artifact"].endswith("bridge/turn-send/stdout.txt")
    assert details["output_traces"][1]["stderr_artifact"].endswith("bridge/turn-send/stderr.txt")


def test_bridge_status_returns_supervision_snapshot(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-status",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-status",
        task_id_factory=lambda: "task-status",
        trace_id_factory=lambda: "trace-status",
    )

    bridge.start(repo_root=repo_root, goal="检查状态", workspace_mode="readonly", supervised=True)
    snapshot = bridge.status(repo_root=repo_root, bridge_id=None)

    assert snapshot["bridge"]["bridge_id"] == "bridge-status"
    assert snapshot["bridge"]["session_id"] == "session-status"
    assert snapshot["latest_turn"]["turn_id"] == "turn-start"
    assert snapshot["session"]["session_id"] == "session-status"
    assert snapshot["latest_verification"] is None
    assert snapshot["latest_challenge"] is None
    assert snapshot["suggested_next"]["needs_codex_review"] is True
    assert snapshot["suggested_next"]["verification_failed"] is False
    assert snapshot["suggested_next"]["challenge_pending"] is False


def test_bridge_challenge_send_records_challenge_and_sends_repair_goal(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    responses = [
        CompletedProcess(
            ["claude"],
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"第一轮。"}',
            stderr="",
        ),
        CompletedProcess(
            ["claude"],
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"修复完成。"}',
            stderr="",
        ),
    ]
    commands = []

    def fake_runner(command, **kwargs):
        commands.append(list(command))
        return responses.pop(0)

    turn_ids = iter(["turn-start", "turn-repair"])
    trace_ids = iter(["trace-start", "trace-repair"])
    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-challenge",
        turn_id_factory=lambda: next(turn_ids),
        session_id_factory=lambda: "session-challenge",
        task_id_factory=lambda: "task-challenge",
        trace_id_factory=lambda: next(trace_ids),
        challenge_id_factory=lambda: "challenge-bridge",
    )

    bridge.start(repo_root=repo_root, goal="实现功能", workspace_mode="shared", supervised=True)
    result = bridge.challenge(
        repo_root=repo_root,
        bridge_id=None,
        summary="缺少验证证据",
        repair_goal="补充测试并汇报验证结果",
        send=True,
    )

    assert result["challenge"]["challenge_id"] == "challenge-bridge"
    assert result["bridge"]["latest_challenge_id"] == "challenge-bridge"
    assert result["latest_turn"]["turn_id"] == "turn-repair"
    assert "补充测试并汇报验证结果" in commands[1]
    details = recorder.read_session("session-challenge")
    assert details["challenges"][0]["summary"] == "缺少验证证据"
    assert details["turns"][1]["phase"] == "challenge"
    assert details["turns"][1]["round_index"] == 1
    assert details["turns"][2]["phase"] == "execute"
    assert details["turns"][2]["round_index"] == 1
    snapshot = bridge.status(repo_root=repo_root, bridge_id=None)
    assert snapshot["latest_challenge"]["challenge_id"] == "challenge-bridge"
    assert snapshot["suggested_next"]["challenge_pending"] is True


def test_bridge_challenge_send_failure_returns_recovery_signal(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    calls = []

    def fake_runner(command, **kwargs):
        calls.append(list(command))
        if len(calls) == 2:
            raise RuntimeError("send failed")
        return CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"第一轮。"}',
            stderr="",
        )

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-send-failure",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-send-failure",
        task_id_factory=lambda: "task-send-failure",
        trace_id_factory=lambda: "trace-start",
        challenge_id_factory=lambda: "challenge-send-failure",
    )

    bridge.start(repo_root=repo_root, goal="实现功能", workspace_mode="shared", supervised=True)
    result = bridge.challenge(
        repo_root=repo_root,
        bridge_id=None,
        summary="缺少验证证据",
        repair_goal="补充测试并汇报验证结果",
        send=True,
    )

    assert result["challenge"]["challenge_id"] == "challenge-send-failure"
    assert result["latest_turn"] is None
    assert "send failed" in result["send_error"]
    assert result["bridge"]["latest_challenge_id"] == "challenge-send-failure"
    details = recorder.read_session("session-send-failure")
    assert details["challenges"][0]["challenge_id"] == "challenge-send-failure"
    log_text = (repo_root / ".orchestrator" / "claude-bridge" / "bridge-send-failure" / "bridge.log").read_text(
        encoding="utf-8"
    )
    assert "Challenge repair send failed" in log_text
    assert "send failed" in log_text


def test_bridge_terminal_status_suppresses_pending_challenge(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-terminal-challenge",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-terminal-challenge",
        task_id_factory=lambda: "task-terminal-challenge",
        trace_id_factory=lambda: "trace-terminal-challenge",
        challenge_id_factory=lambda: "challenge-terminal",
    )

    bridge.start(repo_root=repo_root, goal="最终确认", workspace_mode="readonly", supervised=True)
    bridge.challenge(
        repo_root=repo_root,
        bridge_id=None,
        summary="缺少验证证据",
        repair_goal="补充测试并汇报验证结果",
    )

    before_accept = bridge.status(repo_root=repo_root, bridge_id=None)
    assert before_accept["suggested_next"]["challenge_pending"] is True

    bridge.accept(repo_root=repo_root, bridge_id=None, summary="Codex reviewed and accepted")

    after_accept = bridge.status(repo_root=repo_root, bridge_id=None)
    assert after_accept["bridge"]["latest_challenge_id"] == "challenge-terminal"
    assert after_accept["suggested_next"]["challenge_pending"] is False


def test_bridge_accept_and_needs_human_finalize_supervised_session(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-final",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-final",
        task_id_factory=lambda: "task-final",
        trace_id_factory=lambda: "trace-final",
    )

    bridge.start(repo_root=repo_root, goal="最终确认", workspace_mode="readonly", supervised=True)
    accepted = bridge.accept(repo_root=repo_root, bridge_id=None, summary="Codex reviewed and accepted")

    assert accepted["bridge"]["status"] == "accepted"
    assert recorder.read_session("session-final")["session"]["status"] == "accepted"

    accepted_again = bridge.accept(repo_root=repo_root, bridge_id=None, summary="Codex reviewed and accepted again")
    assert accepted_again["bridge"]["status"] == "accepted"
    assert recorder.read_session("session-final")["session"]["status"] == "accepted"

    try:
        bridge.needs_human(repo_root=repo_root, bridge_id=None, summary="Need user decision")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    details = recorder.read_session("session-final")
    assert details["session"]["status"] == "accepted"
    assert details["final_report"]["final_summary"] == "Codex reviewed and accepted"


def test_bridge_finalized_bridge_rejects_send_challenge_and_verify(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    verification_runner = FakeBridgeVerificationRunner(recorder, [True])
    runner_calls = []

    def fake_runner(command, **kwargs):
        runner_calls.append(list(command))
        return CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        )

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        session_recorder=recorder,
        verification_runner=verification_runner,
        bridge_id_factory=lambda: "bridge-finalized-guard",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-finalized-guard",
        task_id_factory=lambda: "task-finalized-guard",
        trace_id_factory=lambda: "trace-finalized-guard",
        challenge_id_factory=lambda: "challenge-after-final",
    )

    bridge.start(repo_root=repo_root, goal="最终确认", workspace_mode="readonly", supervised=True)
    bridge.accept(repo_root=repo_root, bridge_id=None, summary="accepted")

    try:
        bridge.send(repo_root=repo_root, bridge_id=None, message="继续")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    try:
        bridge.challenge(repo_root=repo_root, bridge_id=None, summary="late", repair_goal="repair")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    try:
        bridge.verify(repo_root=repo_root, bridge_id=None, command="pytest -q")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    snapshot = bridge.status(repo_root=repo_root, bridge_id=None)
    assert snapshot["bridge"]["status"] == "accepted"
    assert recorder.read_session("session-finalized-guard")["challenges"] == []
    assert len(runner_calls) == 1
    assert verification_runner.commands == []


def test_bridge_finalization_respects_terminal_linked_session_status(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-session-terminal",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-terminal",
        task_id_factory=lambda: "task-terminal",
        trace_id_factory=lambda: "trace-terminal",
    )

    bridge.start(repo_root=repo_root, goal="最终确认", workspace_mode="readonly", supervised=True)
    recorder.finalize_session(
        "session-terminal",
        SessionStatus.ACCEPTED,
        "session already accepted",
        current_round=1,
    )

    accepted = bridge.accept(repo_root=repo_root, bridge_id=None, summary="should not rewrite")
    details = recorder.read_session("session-terminal")

    assert accepted["bridge"]["status"] == "accepted"
    assert details["session"]["status"] == "accepted"
    assert details["final_report"]["final_summary"] == "session already accepted"

    try:
        bridge.needs_human(repo_root=repo_root, bridge_id=None, summary="conflicting")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    details = recorder.read_session("session-terminal")
    assert details["session"]["status"] == "accepted"
    assert details["final_report"]["final_summary"] == "session already accepted"


def test_bridge_rejects_mutations_when_linked_session_is_finalized(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    verification_runner = FakeBridgeVerificationRunner(recorder, [True])
    runner_calls = []

    def fake_runner(command, **kwargs):
        runner_calls.append(list(command))
        return CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        )

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        session_recorder=recorder,
        verification_runner=verification_runner,
        bridge_id_factory=lambda: "bridge-linked-finalized",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-linked-finalized",
        task_id_factory=lambda: "task-linked-finalized",
        trace_id_factory=lambda: "trace-linked-finalized",
        challenge_id_factory=lambda: "challenge-linked-finalized",
    )

    bridge.start(repo_root=repo_root, goal="最终确认", workspace_mode="readonly", supervised=True)
    recorder.finalize_session(
        "session-linked-finalized",
        SessionStatus.ACCEPTED,
        "external accepted",
        current_round=1,
    )

    try:
        bridge.send(repo_root=repo_root, bridge_id=None, message="继续")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    try:
        bridge.challenge(repo_root=repo_root, bridge_id=None, summary="late", repair_goal="repair")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    try:
        bridge.verify(repo_root=repo_root, bridge_id=None, command="pytest -q")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    details = recorder.read_session("session-linked-finalized")
    assert details["challenges"] == []
    assert len(runner_calls) == 1
    assert verification_runner.commands == []


def test_bridge_rejects_mutations_when_linked_session_failed(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    verification_runner = FakeBridgeVerificationRunner(recorder, [True])
    runner_calls = []

    def fake_runner(command, **kwargs):
        runner_calls.append(list(command))
        return CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        )

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        session_recorder=recorder,
        verification_runner=verification_runner,
        bridge_id_factory=lambda: "bridge-linked-failed",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-linked-failed",
        task_id_factory=lambda: "task-linked-failed",
        trace_id_factory=lambda: "trace-linked-failed",
        challenge_id_factory=lambda: "challenge-linked-failed",
    )

    bridge.start(repo_root=repo_root, goal="最终确认", workspace_mode="readonly", supervised=True)
    recorder.finalize_session(
        "session-linked-failed",
        SessionStatus.FAILED,
        "external failed",
        current_round=1,
    )

    try:
        bridge.send(repo_root=repo_root, bridge_id=None, message="继续")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    try:
        bridge.challenge(repo_root=repo_root, bridge_id=None, summary="late", repair_goal="repair")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    try:
        bridge.verify(repo_root=repo_root, bridge_id=None, command="pytest -q")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    try:
        bridge.accept(repo_root=repo_root, bridge_id=None, summary="should not overwrite")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    details = recorder.read_session("session-linked-failed")
    assert details["challenges"] == []
    assert details["final_report"]["final_summary"] == "external failed"
    assert len(runner_calls) == 1
    assert verification_runner.commands == []


def test_bridge_accept_rejects_blocked_linked_session_without_overwriting_report(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-linked-blocked",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-linked-blocked",
        task_id_factory=lambda: "task-linked-blocked",
        trace_id_factory=lambda: "trace-linked-blocked",
    )

    bridge.start(repo_root=repo_root, goal="最终确认", workspace_mode="readonly", supervised=True)
    recorder.finalize_session(
        "session-linked-blocked",
        SessionStatus.BLOCKED,
        "external blocked",
        current_round=1,
    )

    try:
        bridge.accept(repo_root=repo_root, bridge_id=None, summary="should not overwrite")
    except ValueError as exc:
        assert "already finalized" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    details = recorder.read_session("session-linked-blocked")
    assert details["session"]["status"] == "blocked"
    assert details["final_report"]["final_summary"] == "external blocked"


def test_bridge_finalization_repairs_terminal_bridge_running_session(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-terminal-record",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-running-linked",
        task_id_factory=lambda: "task-running-linked",
        trace_id_factory=lambda: "trace-running-linked",
    )

    bridge.start(repo_root=repo_root, goal="最终确认", workspace_mode="readonly", supervised=True)
    record_path = repo_root / ".orchestrator" / "claude-bridge" / "bridge-terminal-record" / "record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["status"] = "accepted"
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    accepted = bridge.accept(repo_root=repo_root, bridge_id=None, summary="repair linked session")
    details = recorder.read_session("session-running-linked")

    assert accepted["bridge"]["status"] == "accepted"
    assert details["session"]["status"] == "accepted"
    assert details["final_report"]["final_summary"] == "repair linked session"


def test_bridge_challenge_requires_supervised_bridge(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        bridge_id_factory=lambda: "bridge-unsupervised",
        turn_id_factory=lambda: "turn-start",
    )

    bridge.start(repo_root=repo_root, goal="普通 bridge", workspace_mode="readonly")

    try:
        bridge.challenge(
            repo_root=repo_root,
            bridge_id=None,
            summary="should fail",
            repair_goal="nope",
        )
    except ValueError as exc:
        assert "not supervised" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_bridge_verify_records_verification_for_latest_turn(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    verification_runner = FakeBridgeVerificationRunner(recorder, [False])

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        verification_runner=verification_runner,
        bridge_id_factory=lambda: "bridge-verify",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-verify",
        task_id_factory=lambda: "task-verify",
        trace_id_factory=lambda: "trace-verify",
    )

    bridge.start(repo_root=repo_root, goal="运行验证", workspace_mode="readonly", supervised=True)
    result = bridge.verify(repo_root=repo_root, bridge_id=None, command="pytest -q")

    assert result["verification"]["passed"] is False
    assert result["verification"]["turn_id"] == "turn-start"
    assert result["bridge"]["latest_verification_status"] == "failed"
    assert verification_runner.commands == ["pytest -q"]

    snapshot = bridge.status(repo_root=repo_root, bridge_id=None)
    assert snapshot["latest_verification"]["passed"] is False
    assert snapshot["suggested_next"]["verification_failed"] is True

    log_text = (repo_root / ".orchestrator" / "claude-bridge" / "bridge-verify" / "bridge.log").read_text(
        encoding="utf-8"
    )
    assert "[VERIFY] FAIL" in log_text
    assert "pytest -q" in log_text

    details = recorder.read_session("session-verify")
    assert details["verifications"][0]["command"] == "pytest -q"
    assert details["turns"][-1]["phase"] == "final_verify"
    assert details["turns"][-1]["round_index"] == 1


def test_bridge_verify_rejects_unknown_explicit_turn_id(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    verification_runner = FakeBridgeVerificationRunner(recorder, [True])

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        verification_runner=verification_runner,
        bridge_id_factory=lambda: "bridge-missing-turn",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-missing-turn",
        task_id_factory=lambda: "task-missing-turn",
        trace_id_factory=lambda: "trace-missing-turn",
    )

    bridge.start(repo_root=repo_root, goal="验证未知 turn", workspace_mode="readonly", supervised=True)

    try:
        bridge.verify(repo_root=repo_root, bridge_id=None, turn_id="missing-turn", command="pytest -q")
    except ValueError as exc:
        assert "unknown bridge turn" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    assert verification_runner.commands == []
    assert recorder.read_session("session-missing-turn")["verifications"] == []


def test_bridge_verify_records_passing_status(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    verification_runner = FakeBridgeVerificationRunner(recorder, [True])

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        verification_runner=verification_runner,
        bridge_id_factory=lambda: "bridge-pass",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-pass",
        task_id_factory=lambda: "task-pass",
        trace_id_factory=lambda: "trace-pass",
    )

    bridge.start(repo_root=repo_root, goal="运行通过验证", workspace_mode="readonly", supervised=True)
    result = bridge.verify(repo_root=repo_root, bridge_id=None, command="pytest -q")

    assert result["verification"]["passed"] is True
    assert result["bridge"]["latest_verification_status"] == "passed"


def test_bridge_verify_records_blocked_status_for_policy_block(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    verification_runner = FakeBlockedBridgeVerificationRunner(recorder)

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        verification_runner=verification_runner,
        bridge_id_factory=lambda: "bridge-blocked",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-blocked",
        task_id_factory=lambda: "task-blocked",
        trace_id_factory=lambda: "trace-blocked",
    )

    bridge.start(repo_root=repo_root, goal="运行被阻止的验证", workspace_mode="readonly", supervised=True)
    result = bridge.verify(repo_root=repo_root, bridge_id=None, command="bash -lc 'git reset --hard'")

    assert result["verification"]["passed"] is False
    assert result["verification"]["exit_code"] is None
    assert result["verification"]["summary"].startswith("command blocked: ")
    assert result["bridge"]["latest_verification_status"] == "blocked"

    snapshot = bridge.status(repo_root=repo_root, bridge_id=None)
    assert snapshot["bridge"]["latest_verification_status"] == "blocked"
    assert snapshot["latest_verification"]["exit_code"] is None

    details = recorder.read_session("session-blocked")
    assert details["turns"][-1]["decision"] == "blocked"
