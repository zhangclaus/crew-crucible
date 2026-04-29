import sys
from pathlib import Path

from codex_claude_orchestrator.models import SessionRecord
from codex_claude_orchestrator.policy_gate import PolicyGate
from codex_claude_orchestrator.session_recorder import SessionRecorder
from codex_claude_orchestrator.verification_runner import VerificationRunner


def test_verification_runner_records_passing_command_and_artifacts(tmp_path: Path):
    recorder = SessionRecorder(tmp_path / ".orchestrator")
    recorder.start_session(
        SessionRecord(
            session_id="session-1",
            root_task_id="task-1",
            goal="Run verification",
            assigned_agent="codex",
        )
    )
    runner = VerificationRunner(
        repo_root=tmp_path,
        session_recorder=recorder,
        policy_gate=PolicyGate(),
    )

    record = runner.run(
        session_id="session-1",
        turn_id="turn-1",
        command=f"{sys.executable} -c \"print('verification ok')\"",
    )

    assert record.session_id == "session-1"
    assert record.turn_id == "turn-1"
    assert record.passed is True
    assert record.exit_code == 0
    assert record.summary == "command passed: exit code 0"
    assert record.stdout_artifact is not None
    assert record.stderr_artifact is not None
    assert Path(record.stdout_artifact).read_text(encoding="utf-8") == "verification ok\n"
    assert Path(record.stderr_artifact).read_text(encoding="utf-8") == ""

    details = recorder.read_session("session-1")
    assert details["verifications"][0]["verification_id"] == record.verification_id
    assert details["verifications"][0]["passed"] is True
    assert sorted(details["artifacts"]) == [
        f"verification/{record.verification_id}/stderr.txt",
        f"verification/{record.verification_id}/stdout.txt",
    ]


def test_verification_runner_records_blocked_command_without_executing(tmp_path: Path):
    recorder = SessionRecorder(tmp_path / ".orchestrator")
    recorder.start_session(
        SessionRecord(
            session_id="session-1",
            root_task_id="task-1",
            goal="Run verification",
            assigned_agent="codex",
        )
    )
    runner = VerificationRunner(
        repo_root=tmp_path,
        session_recorder=recorder,
        policy_gate=PolicyGate(),
    )

    record = runner.run(
        session_id="session-1",
        turn_id="turn-1",
        command="rm -rf should-not-exist",
    )

    assert record.passed is False
    assert record.exit_code is None
    assert record.summary == "command blocked: blocked command prefix: rm -rf"
    assert record.stdout_artifact is not None
    assert record.stderr_artifact is not None
    assert Path(record.stdout_artifact).read_text(encoding="utf-8") == ""
    assert Path(record.stderr_artifact).read_text(encoding="utf-8") == "blocked command prefix: rm -rf\n"
    assert not (tmp_path / "should-not-exist").exists()

    details = recorder.read_session("session-1")
    assert details["verifications"][0]["verification_id"] == record.verification_id
    assert details["verifications"][0]["passed"] is False
    assert sorted(details["artifacts"]) == [
        f"verification/{record.verification_id}/stderr.txt",
        f"verification/{record.verification_id}/stdout.txt",
    ]
