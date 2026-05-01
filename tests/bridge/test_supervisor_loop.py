from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

from codex_claude_orchestrator.bridge.supervisor_loop import BridgeSupervisorLoop
from codex_claude_orchestrator.bridge.claude_bridge import ClaudeBridge
from codex_claude_orchestrator.core.models import VerificationKind, VerificationRecord
from codex_claude_orchestrator.state.session_recorder import SessionRecorder


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


def test_supervisor_accepts_after_verification_passes(tmp_path: Path):
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
        bridge_id_factory=lambda: "bridge-auto-accept",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-auto-accept",
        task_id_factory=lambda: "task-auto-accept",
        trace_id_factory=lambda: "trace-auto-accept",
    )
    bridge.start(repo_root=repo_root, goal="实现功能", workspace_mode="shared", supervised=True)

    result = BridgeSupervisorLoop(bridge).supervise(
        repo_root=repo_root,
        bridge_id=None,
        verification_commands=["pytest -q"],
        max_rounds=2,
        poll_interval_seconds=0,
    )

    assert result["status"] == "accepted"
    assert result["accepted"] is True
    assert result["needs_human"] is False
    assert result["rounds_used"] == 1
    assert [event["action"] for event in result["events"]] == ["verify", "accept"]
    assert verification_runner.commands == ["pytest -q"]
    assert recorder.read_session("session-auto-accept")["session"]["status"] == "accepted"


def test_supervisor_rejects_missing_verification_commands(tmp_path: Path):
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
        bridge_id_factory=lambda: "bridge-missing-verification",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-missing-verification",
        task_id_factory=lambda: "task-missing-verification",
        trace_id_factory=lambda: "trace-missing-verification",
    )
    bridge.start(repo_root=repo_root, goal="实现功能", workspace_mode="shared", supervised=True)

    try:
        BridgeSupervisorLoop(bridge).supervise(
            repo_root=repo_root,
            bridge_id=None,
            verification_commands=[],
            max_rounds=2,
            poll_interval_seconds=0,
        )
    except ValueError as exc:
        assert "verification command" in str(exc)
    else:
        raise AssertionError("expected ValueError")

    assert recorder.read_session("session-missing-verification")["session"]["status"] == "running"


def test_supervisor_rejects_unsupervised_bridge(tmp_path: Path):
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
    bridge.start(repo_root=repo_root, goal="普通 bridge", workspace_mode="readonly", supervised=False)

    try:
        BridgeSupervisorLoop(bridge).supervise(
            repo_root=repo_root,
            bridge_id=None,
            verification_commands=["pytest -q"],
            max_rounds=2,
            poll_interval_seconds=0,
        )
    except ValueError as exc:
        assert "not supervised" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_supervisor_challenges_failed_verification_until_acceptance(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    verification_runner = FakeBridgeVerificationRunner(recorder, [False, True])
    responses = [
        CompletedProcess(
            ["claude"],
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"初版完成。"}',
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
        verification_runner=verification_runner,
        bridge_id_factory=lambda: "bridge-auto-repair",
        turn_id_factory=lambda: next(turn_ids),
        session_id_factory=lambda: "session-auto-repair",
        task_id_factory=lambda: "task-auto-repair",
        trace_id_factory=lambda: next(trace_ids),
        challenge_id_factory=lambda: "challenge-auto-repair",
    )
    bridge.start(repo_root=repo_root, goal="实现功能", workspace_mode="shared", supervised=True)

    result = BridgeSupervisorLoop(bridge).supervise(
        repo_root=repo_root,
        bridge_id=None,
        verification_commands=["pytest -q"],
        max_rounds=2,
        poll_interval_seconds=0,
    )

    assert result["status"] == "accepted"
    assert result["rounds_used"] == 2
    assert [event["action"] for event in result["events"]] == ["verify", "challenge", "verify", "accept"]
    assert "pytest -q" in result["events"][1]["repair_goal"]
    assert any("pytest -q" in part for part in commands[1])
    details = recorder.read_session("session-auto-repair")
    assert details["session"]["status"] == "accepted"
    assert details["challenges"][0]["challenge_id"] == "challenge-auto-repair"


def test_supervisor_marks_needs_human_after_round_budget(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    verification_runner = FakeBridgeVerificationRunner(recorder, [False])
    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成但测试失败。"}',
            stderr="",
        ),
        session_recorder=recorder,
        verification_runner=verification_runner,
        bridge_id_factory=lambda: "bridge-auto-human",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-auto-human",
        task_id_factory=lambda: "task-auto-human",
        trace_id_factory=lambda: "trace-auto-human",
    )
    bridge.start(repo_root=repo_root, goal="实现功能", workspace_mode="shared", supervised=True)

    result = BridgeSupervisorLoop(bridge).supervise(
        repo_root=repo_root,
        bridge_id=None,
        verification_commands=["pytest -q"],
        max_rounds=1,
        poll_interval_seconds=0,
    )

    assert result["status"] == "needs_human"
    assert result["accepted"] is False
    assert result["needs_human"] is True
    assert result["rounds_used"] == 1
    assert [event["action"] for event in result["events"]] == ["verify", "needs_human"]
    details = recorder.read_session("session-auto-human")
    assert details["session"]["status"] == "needs_human"
    assert "round budget exhausted" in details["final_report"]["final_summary"]
