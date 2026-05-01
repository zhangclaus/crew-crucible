from codex_claude_orchestrator.core.models import (
    ChallengeRecord,
    ChallengeType,
    DispatchReport,
    EvaluationOutcome,
    LearningNote,
    NextAction,
    OutputTrace,
    SessionRecord,
    SessionStatus,
    SkillRecord,
    SkillStatus,
    TaskRecord,
    TaskStatus,
    TurnPhase,
    TurnRecord,
    VerificationKind,
    VerificationRecord,
    WorkspaceMode,
)


def test_task_record_to_dict_normalizes_enum_fields():
    task = TaskRecord(
        task_id="task-1",
        parent_task_id=None,
        origin="user",
        assigned_agent="claude",
        goal="Review the repository",
        task_type="review",
        scope="repo root",
        workspace_mode=WorkspaceMode.ISOLATED,
        status=TaskStatus.QUEUED,
        expected_output_schema={"type": "object"},
    )

    data = task.to_dict()

    assert data["workspace_mode"] == "isolated"
    assert data["status"] == "queued"
    assert data["shared_write_allowed"] is False
    assert data["expected_output_schema"]["type"] == "object"


def test_session_record_to_dict_normalizes_v2_session_fields():
    session = SessionRecord(
        session_id="session-1",
        root_task_id="task-1",
        repo="/tmp/repo",
        goal="Ship V2 sessions",
        assigned_agent="codex",
        status=SessionStatus.RUNNING,
        workspace_mode=WorkspaceMode.SHARED,
        max_rounds=3,
        current_round=2,
        acceptance_criteria=["feature works"],
        failure_criteria=["tests fail"],
        verification_commands=["pytest"],
        generated_checks=["try the edge case"],
        active_skill_ids=["skill-1"],
        final_summary="still working",
        ended_at="2026-04-29T00:00:00+00:00",
    )

    data = session.to_dict()

    assert data["session_id"] == "session-1"
    assert data["root_task_id"] == "task-1"
    assert data["repo"] == "/tmp/repo"
    assert data["status"] == "running"
    assert data["workspace_mode"] == "shared"
    assert data["max_rounds"] == 3
    assert data["current_round"] == 2
    assert data["acceptance_criteria"] == ["feature works"]
    assert data["failure_criteria"] == ["tests fail"]
    assert data["verification_commands"] == ["pytest"]
    assert data["generated_checks"] == ["try the edge case"]
    assert data["active_skill_ids"] == ["skill-1"]
    assert data["final_summary"] == "still working"
    assert data["ended_at"] == "2026-04-29T00:00:00+00:00"
    assert isinstance(data["created_at"], str)
    assert isinstance(data["updated_at"], str)


def test_turn_record_to_dict_normalizes_phase_and_payload():
    turn = TurnRecord(
        turn_id="turn-1",
        session_id="session-1",
        round_index=1,
        phase=TurnPhase.EXECUTE,
        task_id="task-1",
        run_id="run-1",
        from_agent="codex",
        to_agent="claude",
        message="Please implement this",
        decision="continue",
        summary="initial execution",
        payload={"next": TurnPhase.LIGHT_VERIFY},
    )

    data = turn.to_dict()

    assert data["turn_id"] == "turn-1"
    assert data["phase"] == "execute"
    assert data["from_agent"] == "codex"
    assert data["to_agent"] == "claude"
    assert data["message"] == "Please implement this"
    assert data["decision"] == "continue"
    assert data["payload"]["next"] == "light_verify"
    assert isinstance(data["created_at"], str)


def test_output_trace_to_dict_normalizes_artifacts_and_evaluation():
    trace = OutputTrace(
        trace_id="trace-1",
        session_id="session-1",
        turn_id="turn-1",
        run_id="run-1",
        task_id="task-1",
        agent="claude",
        adapter="ClaudeCliAdapter",
        prompt_artifact="prompt.txt",
        command=["claude", "--print"],
        stdout_artifact="stdout.txt",
        stderr_artifact="stderr.txt",
        structured_output_artifact="structured_output.json",
        policy_summary="allowed",
        display_summary="worker changed two files",
        output_summary="worker changed two files",
        artifact_paths=[".orchestrator/runs/run-1/stdout.txt"],
        changed_files=["src/app.py"],
        evaluation=EvaluationOutcome(
            accepted=True,
            next_action=NextAction.ACCEPT,
            summary="accepted",
        ),
    )

    data = trace.to_dict()

    assert data["trace_id"] == "trace-1"
    assert data["agent"] == "claude"
    assert data["adapter"] == "ClaudeCliAdapter"
    assert data["prompt_artifact"] == "prompt.txt"
    assert data["command"] == ["claude", "--print"]
    assert data["stdout_artifact"] == "stdout.txt"
    assert data["stderr_artifact"] == "stderr.txt"
    assert data["structured_output_artifact"] == "structured_output.json"
    assert data["policy_summary"] == "allowed"
    assert data["display_summary"] == "worker changed two files"
    assert data["artifact_paths"] == [".orchestrator/runs/run-1/stdout.txt"]
    assert data["changed_files"] == ["src/app.py"]
    assert data["evaluation"]["accepted"] is True
    assert data["evaluation"]["next_action"] == "accept"


def test_challenge_record_to_dict_normalizes_challenge_type():
    challenge = ChallengeRecord(
        challenge_id="challenge-1",
        session_id="session-1",
        turn_id="turn-1",
        round_index=1,
        challenge_type=ChallengeType.MISSING_TEST,
        question="Which regression test is missing?",
        expected_evidence="pytest output",
        severity=2,
        summary="No regression test covers the failed path",
        evidence={"file": "tests/test_app.py"},
        repair_goal="Add the missing regression test",
    )

    data = challenge.to_dict()

    assert data["challenge_type"] == "missing_test"
    assert data["question"] == "Which regression test is missing?"
    assert data["expected_evidence"] == "pytest output"
    assert data["severity"] == 2
    assert data["evidence"]["file"] == "tests/test_app.py"
    assert data["repair_goal"] == "Add the missing regression test"


def test_verification_record_to_dict_normalizes_kind_and_artifacts():
    verification = VerificationRecord(
        verification_id="verification-1",
        session_id="session-1",
        turn_id="turn-1",
        kind=VerificationKind.COMMAND,
        command="pytest tests/test_models.py",
        passed=True,
        exit_code=0,
        summary="all model tests passed",
        stdout_artifact="stdout.txt",
        stderr_artifact="stderr.txt",
    )

    data = verification.to_dict()

    assert data["kind"] == "command"
    assert data["command"] == "pytest tests/test_models.py"
    assert data["passed"] is True
    assert data["exit_code"] == 0
    assert data["stdout_artifact"] == "stdout.txt"
    assert data["stderr_artifact"] == "stderr.txt"


def test_learning_note_and_skill_record_to_dict_normalize_skill_status():
    learning = LearningNote(
        note_id="learning-1",
        learning_id="learning-1",
        session_id="session-1",
        source_turn_ids=["turn-1"],
        pattern="repairs without tests",
        trigger_conditions=["repair task"],
        evidence_summary="challenge identified missing regression coverage",
        confidence=0.8,
        challenge_ids=["challenge-1"],
        summary="Require regression tests for repaired behavior",
        proposed_skill_name="regression-test-check",
    )
    skill = SkillRecord(
        skill_id="skill-1",
        name="regression-test-check",
        version="0.1.0",
        status=SkillStatus.PENDING,
        source_session_id="session-1",
        learning_note_id="learning-1",
        trigger_conditions=["repair task"],
        validation_summary="pending validation",
        approval_mode="human",
        path=".orchestrator/skills/pending/regression-test-check/SKILL.md",
        summary="Check repairs include regression coverage",
    )

    learning_data = learning.to_dict()
    skill_data = skill.to_dict()

    assert learning_data["challenge_ids"] == ["challenge-1"]
    assert learning_data["source_turn_ids"] == ["turn-1"]
    assert learning_data["pattern"] == "repairs without tests"
    assert learning_data["trigger_conditions"] == ["repair task"]
    assert learning_data["evidence_summary"] == "challenge identified missing regression coverage"
    assert learning_data["confidence"] == 0.8
    assert learning_data["proposed_skill_name"] == "regression-test-check"
    assert skill_data["version"] == "0.1.0"
    assert skill_data["status"] == "pending"
    assert skill_data["source_session_id"] == "session-1"
    assert skill_data["learning_note_id"] == "learning-1"
    assert skill_data["trigger_conditions"] == ["repair task"]
    assert skill_data["validation_summary"] == "pending validation"
    assert skill_data["approval_mode"] == "human"


def test_dispatch_report_to_dict_normalizes_nested_evaluation():
    report = DispatchReport(
        run_id="run-1",
        task_id="task-1",
        evaluation=EvaluationOutcome(
            accepted=False,
            next_action=NextAction.RETRY_WITH_TIGHTER_PROMPT,
            summary="needs repair",
        ),
    )

    data = report.to_dict()

    assert data == {
        "run_id": "run-1",
        "task_id": "task-1",
        "evaluation": {
            "accepted": False,
            "next_action": "retry_with_tighter_prompt",
            "summary": "needs repair",
            "failure_class": None,
            "needs_human": False,
        },
    }
