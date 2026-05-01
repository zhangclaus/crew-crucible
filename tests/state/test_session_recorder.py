from pathlib import Path

from codex_claude_orchestrator.core.models import (
    ChallengeRecord,
    ChallengeType,
    EvaluationOutcome,
    LearningNote,
    NextAction,
    OutputTrace,
    SessionRecord,
    SessionStatus,
    TurnPhase,
    TurnRecord,
    VerificationKind,
    VerificationRecord,
    WorkspaceMode,
)
from codex_claude_orchestrator.state.session_recorder import SessionRecorder


def test_session_recorder_persists_session_streams_final_report_and_artifacts(tmp_path: Path):
    recorder = SessionRecorder(tmp_path / ".orchestrator")
    session = SessionRecord(
        session_id="session-1",
        root_task_id="task-1",
        repo="/tmp/repo",
        goal="Implement sessions",
        assigned_agent="claude",
        workspace_mode=WorkspaceMode.ISOLATED,
        max_rounds=2,
        acceptance_criteria=["tests pass"],
        verification_commands=["pytest"],
    )
    turn = TurnRecord(
        turn_id="turn-1",
        session_id=session.session_id,
        round_index=1,
        phase=TurnPhase.EXECUTE,
        task_id=session.root_task_id,
        run_id="run-1",
        from_agent="codex",
        to_agent="claude",
        summary="worker executed",
    )
    trace = OutputTrace(
        trace_id="trace-1",
        session_id=session.session_id,
        turn_id=turn.turn_id,
        run_id="run-1",
        task_id=session.root_task_id,
        agent="claude",
        adapter="claude-cli",
        stdout_artifact="stdout.txt",
        stderr_artifact="stderr.txt",
        output_summary="changed one file",
        evaluation=EvaluationOutcome(
            accepted=False,
            next_action=NextAction.RETRY_WITH_TIGHTER_PROMPT,
            summary="needs repair",
        ),
    )
    challenge = ChallengeRecord(
        challenge_id="challenge-1",
        session_id=session.session_id,
        turn_id=turn.turn_id,
        round_index=1,
        challenge_type=ChallengeType.MISSING_TEST,
        summary="missing regression test",
        repair_goal="add a regression test",
    )
    verification = VerificationRecord(
        verification_id="verification-1",
        session_id=session.session_id,
        turn_id=turn.turn_id,
        kind=VerificationKind.COMMAND,
        command="pytest tests/test_session_recorder.py",
        passed=True,
        exit_code=0,
        summary="recorder tests passed",
        stdout_artifact="verify_stdout.txt",
        stderr_artifact="verify_stderr.txt",
    )
    learning_note = LearningNote(
        note_id="learning-1",
        session_id=session.session_id,
        challenge_ids=[challenge.challenge_id],
        summary="repairs should include regression tests",
        proposed_skill_name="regression-test-check",
    )

    run_dir = recorder.start_session(session)
    recorder.append_turn(session.session_id, turn)
    recorder.append_output_trace(session.session_id, trace)
    recorder.append_challenge(session.session_id, challenge)
    recorder.append_verification(session.session_id, verification)
    recorder.append_learning_note(session.session_id, learning_note)
    artifact_path = recorder.write_text_artifact(session.session_id, "logs/stdout.txt", "hello\n")
    recorder.finalize_session(session.session_id, SessionStatus.ACCEPTED, "session accepted")

    session_dir = tmp_path / ".orchestrator" / "sessions" / "session-1"
    assert run_dir == session_dir
    assert artifact_path == session_dir / "artifacts" / "logs" / "stdout.txt"
    assert (session_dir / "session.json").exists()
    assert (session_dir / "turns.jsonl").exists()
    assert (session_dir / "output_traces.jsonl").exists()
    assert (session_dir / "challenges.jsonl").exists()
    assert (session_dir / "verifications.jsonl").exists()
    assert (session_dir / "learning.json").exists()
    assert (session_dir / "final_report.json").exists()

    details = recorder.read_session(session.session_id)
    assert details["session"]["status"] == "accepted"
    assert details["session"]["final_summary"] == "session accepted"
    assert details["session"]["ended_at"] is not None
    assert details["turns"][0]["phase"] == "execute"
    assert details["output_traces"][0]["evaluation"]["next_action"] == "retry_with_tighter_prompt"
    assert details["challenges"][0]["challenge_type"] == "missing_test"
    assert details["verifications"][0]["passed"] is True
    assert details["learning"][0]["proposed_skill_name"] == "regression-test-check"
    assert details["final_report"]["status"] == "accepted"
    assert details["final_report"]["final_summary"] == "session accepted"
    assert details["artifacts"] == ["logs/stdout.txt"]


def test_session_recorder_lists_sessions_newest_first_and_reads_missing_streams(tmp_path: Path):
    recorder = SessionRecorder(tmp_path / ".orchestrator")
    older = SessionRecord(
        session_id="older-session",
        root_task_id="task-older",
        goal="Older",
        assigned_agent="claude",
        created_at="2026-04-29T00:00:00+00:00",
    )
    newer = SessionRecord(
        session_id="newer-session",
        root_task_id="task-newer",
        goal="Newer",
        assigned_agent="codex",
        status=SessionStatus.BLOCKED,
        created_at="2026-04-29T01:00:00+00:00",
        final_summary="blocked by policy",
    )

    recorder.start_session(older)
    recorder.start_session(newer)

    sessions = recorder.list_sessions()
    details = recorder.read_session("older-session")

    assert [item["session_id"] for item in sessions] == ["newer-session", "older-session"]
    assert sessions[0] == {
        "session_id": "newer-session",
        "root_task_id": "task-newer",
        "goal": "Newer",
        "assigned_agent": "codex",
        "status": "blocked",
        "summary": "blocked by policy",
        "created_at": "2026-04-29T01:00:00+00:00",
        "ended_at": None,
    }
    assert details["session"]["session_id"] == "older-session"
    assert details["turns"] == []
    assert details["output_traces"] == []
    assert details["challenges"] == []
    assert details["verifications"] == []
    assert details["learning"] == []
    assert details["final_report"] is None
    assert details["artifacts"] == []
