from __future__ import annotations

from pathlib import Path

from codex_claude_orchestrator.core.models import (
    DispatchReport,
    EvaluationOutcome,
    FailureClass,
    LearningNote,
    NextAction,
    SessionStatus,
    SkillRecord,
    SkillStatus,
    TaskRecord,
    VerificationKind,
    VerificationRecord,
    WorkspaceMode,
)
from codex_claude_orchestrator.session.engine import SessionEngine
from codex_claude_orchestrator.state.session_recorder import SessionRecorder


def test_accepted_dispatch_without_verification_marks_session_accepted_and_writes_output_trace(tmp_path: Path):
    engine, recorder, supervisor, verification_runner, skill_evolution = _engine(
        tmp_path,
        [_dispatch("run-1", accepted=True, summary="worker finished", changed_files=["app.py"])],
    )

    result = engine.start(
        repo_root=tmp_path,
        goal="Implement feature",
        assigned_agent="claude",
        workspace_mode=WorkspaceMode.ISOLATED,
    )

    details = recorder.read_session(result.session_id)
    assert result.status is SessionStatus.ACCEPTED
    assert details["session"]["status"] == "accepted"
    assert details["session"]["current_round"] == 1
    assert details["session"]["final_summary"] == "worker finished"
    assert len(details["turns"]) == 1
    assert details["turns"][0]["phase"] == "execute"
    assert len(details["output_traces"]) == 1
    trace = details["output_traces"][0]
    assert trace["run_id"] == "run-1"
    assert trace["agent"] == "claude"
    assert trace["adapter"] == "fake-adapter"
    assert trace["command"] == ["fake-agent", "run-1"]
    assert trace["prompt_artifact"] == "prompt.txt"
    assert trace["stdout_artifact"] == "stdout.txt"
    assert trace["stderr_artifact"] == "stderr.txt"
    assert trace["changed_files"] == ["app.py"]
    assert trace["evaluation"]["accepted"] is True
    assert trace["display_summary"] == "worker finished"
    assert verification_runner.commands == []
    assert skill_evolution.learning_notes == []
    assert [task.parent_task_id for task in supervisor.tasks] == [None]


def test_accepted_dispatch_with_passing_verification_marks_session_accepted(tmp_path: Path):
    engine, recorder, supervisor, verification_runner, skill_evolution = _engine(
        tmp_path,
        [_dispatch("run-1", accepted=True, summary="ready for verification")],
        verification_results=[True],
    )

    result = engine.start(
        repo_root=tmp_path,
        goal="Implement feature",
        assigned_agent="claude",
        workspace_mode="isolated",
        verification_commands=["pytest -q"],
    )

    details = recorder.read_session(result.session_id)
    assert result.status is SessionStatus.ACCEPTED
    assert details["session"]["status"] == "accepted"
    assert details["session"]["final_summary"] == "verification passed"
    assert verification_runner.commands == ["pytest -q"]
    assert details["verifications"][0]["passed"] is True
    assert details["challenges"] == []
    assert skill_evolution.learning_notes == []
    assert supervisor.tasks[0].verification_expectations == ["pytest -q"]


def test_failed_dispatch_retries_appends_challenge_and_creates_pending_skill(tmp_path: Path):
    engine, recorder, supervisor, verification_runner, skill_evolution = _engine(
        tmp_path,
        [
            _dispatch("run-1", accepted=False, summary="missing tests"),
            _dispatch("run-2", accepted=True, summary="fixed"),
        ],
    )

    result = engine.start(
        repo_root=tmp_path,
        goal="Implement feature",
        assigned_agent="claude",
        workspace_mode=WorkspaceMode.ISOLATED,
        max_rounds=2,
    )

    details = recorder.read_session(result.session_id)
    assert result.status is SessionStatus.ACCEPTED
    assert details["session"]["status"] == "accepted"
    assert details["session"]["current_round"] == 2
    assert len(details["turns"]) == 2
    assert len(details["output_traces"]) == 2
    assert len(details["challenges"]) == 1
    assert details["challenges"][0]["summary"] == "Dispatch was not accepted: missing tests"
    assert "Repair the previous attempt" in details["challenges"][0]["repair_goal"]
    assert len(details["learning"]) == 1
    assert len(skill_evolution.learning_notes) == 1
    assert skill_evolution.learning_notes[0].challenge_ids == [details["challenges"][0]["challenge_id"]]
    assert supervisor.tasks[0].parent_task_id is None
    assert supervisor.tasks[1].parent_task_id == details["session"]["root_task_id"]
    assert "Repair the previous attempt" in supervisor.tasks[1].goal
    assert verification_runner.commands == []


def test_final_verification_failure_retries_when_rounds_remain(tmp_path: Path):
    engine, recorder, supervisor, verification_runner, skill_evolution = _engine(
        tmp_path,
        [
            _dispatch("run-1", accepted=True, summary="first implementation"),
            _dispatch("run-2", accepted=True, summary="repair implementation"),
        ],
        verification_results=[False, True],
    )

    result = engine.start(
        repo_root=tmp_path,
        goal="Implement feature",
        assigned_agent="claude",
        workspace_mode=WorkspaceMode.ISOLATED,
        max_rounds=2,
        verification_commands=["pytest -q"],
    )

    details = recorder.read_session(result.session_id)
    assert result.status is SessionStatus.ACCEPTED
    assert details["session"]["status"] == "accepted"
    assert verification_runner.commands == ["pytest -q", "pytest -q"]
    assert len(details["verifications"]) == 2
    assert [record["passed"] for record in details["verifications"]] == [False, True]
    assert len(details["challenges"]) == 1
    assert details["challenges"][0]["summary"] == "Final verification failed: pytest -q"
    assert len(details["output_traces"]) == 2
    assert len(skill_evolution.learning_notes) == 1
    assert "Verification failed" in supervisor.tasks[1].goal


def test_max_rounds_with_failure_marks_needs_human(tmp_path: Path):
    engine, recorder, supervisor, verification_runner, skill_evolution = _engine(
        tmp_path,
        [_dispatch("run-1", accepted=False, summary="still broken")],
    )

    result = engine.start(
        repo_root=tmp_path,
        goal="Implement feature",
        assigned_agent="claude",
        workspace_mode=WorkspaceMode.ISOLATED,
        max_rounds=1,
    )

    details = recorder.read_session(result.session_id)
    assert result.status is SessionStatus.NEEDS_HUMAN
    assert details["session"]["status"] == "needs_human"
    assert details["session"]["current_round"] == 1
    assert details["session"]["final_summary"] == "still broken"
    assert len(details["output_traces"]) == 1
    assert len(details["challenges"]) == 0
    assert details["learning"] == []
    assert skill_evolution.learning_notes == []
    assert len(supervisor.tasks) == 1
    assert verification_runner.commands == []


class FakeSupervisor:
    def __init__(self, run_recorder: "FakeRunRecorder", script: list[dict]):
        self._run_recorder = run_recorder
        self._script = list(script)
        self.tasks: list[TaskRecord] = []

    def dispatch_with_report(self, task: TaskRecord, repo_root: Path) -> DispatchReport:
        self.tasks.append(task)
        entry = self._script.pop(0)
        evaluation = _evaluation(entry["accepted"], entry["summary"])
        self._run_recorder.record_run(
            run_id=entry["run_id"],
            task=task,
            evaluation=evaluation,
            changed_files=entry.get("changed_files", []),
            command=entry.get("command", ["fake-agent", entry["run_id"]]),
        )
        return DispatchReport(run_id=entry["run_id"], task_id=task.task_id, evaluation=evaluation)


class FakeRunRecorder:
    def __init__(self):
        self._runs: dict[str, dict] = {}

    def record_run(
        self,
        *,
        run_id: str,
        task: TaskRecord,
        evaluation: EvaluationOutcome,
        changed_files: list[str],
        command: list[str],
    ) -> None:
        self._runs[run_id] = {
            "task": task.to_dict(),
            "run": {
                "run_id": run_id,
                "task_id": task.task_id,
                "agent": task.assigned_agent,
                "adapter": "fake-adapter",
                "adapter_invocation": {"command": command},
                "result_summary": evaluation.summary,
            },
            "result": {
                "changed_files": changed_files,
            },
            "evaluation": evaluation.to_dict(),
            "events": [],
            "artifacts": ["prompt.txt", "stdout.txt", "stderr.txt"],
        }

    def read_run(self, run_id: str) -> dict:
        return self._runs[run_id]


class FakeVerificationRunner:
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


class FakeSkillEvolution:
    def __init__(self):
        self.learning_notes: list[LearningNote] = []

    def create_pending_skill(self, learning_note: LearningNote, **kwargs) -> SkillRecord:
        self.learning_notes.append(learning_note)
        return SkillRecord(
            skill_id=f"skill-{len(self.learning_notes)}",
            name=learning_note.proposed_skill_name or "session-learning",
            status=SkillStatus.PENDING,
            source_session_id=learning_note.session_id,
            learning_note_id=learning_note.note_id,
            path="pending/session-learning/SKILL.md",
        )


def _engine(
    tmp_path: Path,
    dispatches: list[dict],
    verification_results: list[bool] | None = None,
) -> tuple[SessionEngine, SessionRecorder, FakeSupervisor, FakeVerificationRunner, FakeSkillEvolution]:
    recorder = SessionRecorder(tmp_path / ".orchestrator")
    run_recorder = FakeRunRecorder()
    supervisor = FakeSupervisor(run_recorder, dispatches)
    verification_runner = FakeVerificationRunner(recorder, verification_results or [])
    skill_evolution = FakeSkillEvolution()
    engine = SessionEngine(
        supervisor=supervisor,
        run_recorder=run_recorder,
        session_recorder=recorder,
        verification_runner=verification_runner,
        skill_evolution=skill_evolution,
    )
    return engine, recorder, supervisor, verification_runner, skill_evolution


def _dispatch(run_id: str, *, accepted: bool, summary: str, changed_files: list[str] | None = None) -> dict:
    return {
        "run_id": run_id,
        "accepted": accepted,
        "summary": summary,
        "changed_files": changed_files or [],
    }


def _evaluation(accepted: bool, summary: str) -> EvaluationOutcome:
    return EvaluationOutcome(
        accepted=accepted,
        next_action=NextAction.ACCEPT if accepted else NextAction.RETRY_WITH_TIGHTER_PROMPT,
        summary=summary,
        failure_class=None if accepted else FailureClass.QUALITY_REJECT,
        needs_human=False,
    )
