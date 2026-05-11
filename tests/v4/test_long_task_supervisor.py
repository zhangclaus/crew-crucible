"""Tests for LongTaskSupervisor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from codex_claude_orchestrator.v4.long_task_models import (
    Briefing,
    Contract,
    PlanAdversaryVerdict,
    ProjectContext,
    ReviewVerdict,
    StagePlan,
    SubTaskRef,
    ThinkResult,
)
from codex_claude_orchestrator.v4.long_task_supervisor import LongTaskSupervisor


# --- Fakes ---


class FakeEventStore:
    """Minimal in-memory event store for testing."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(self, *, stream_id: str, type: str, crew_id: str = "", **kwargs: Any) -> dict[str, Any]:
        event = {"stream_id": stream_id, "type": type, "crew_id": crew_id, **kwargs}
        self.events.append(event)
        return event

    def list_stream(self, stream_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        return [e for e in self.events if e["stream_id"] == stream_id]


class FakeController:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self, **kwargs: Any) -> None:
        self.started = True

    def stop(self, **kwargs: Any) -> None:
        self.stopped = True


class FakeSupervisor:
    def __init__(self) -> None:
        self.registered_workers: list[Any] = []

    def register_worker(self, spec: Any) -> None:
        self.registered_workers.append(spec)


# --- ThinkResult fixtures ---


def make_think_result(num_stages: int = 2) -> ThinkResult:
    stages = []
    for i in range(1, num_stages + 1):
        stages.append(
            StagePlan(
                stage_id=i,
                goal=f"Stage {i} goal",
                acceptance_criteria=[f"Stage {i} criterion 1", f"Stage {i} criterion 2"],
                contract=Contract(conventions=["use pytest"]),
                sub_tasks=[
                    SubTaskRef(
                        task_id=f"{i}a",
                        role="backend-developer",
                        goal=f"Stage {i} subtask a",
                        write_scope=[f"src/module{i}.py"],
                    )
                ],
                dependencies=[i - 1] if i > 1 else [],
            )
        )
    return ThinkResult(
        spec="Test spec",
        stages=stages,
        contract=Contract(conventions=["use pytest"]),
        project_context=ProjectContext(tech_stack=["Python"]),
        acceptance_criteria=["all tests pass"],
        open_questions=[],
    )


# --- Tests ---


class TestLoadAndValidateThinkResult:
    def test_valid_think_result(self, tmp_path: Path):
        tr = make_think_result()
        path = tmp_path / "think_result.json"
        path.write_text(json.dumps(tr.to_dict()))

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        result = supervisor.load_and_validate_think_result(path)
        assert result.spec == "Test spec"
        assert len(result.stages) == 2

    def test_missing_file_raises(self, tmp_path: Path):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        with pytest.raises(ValueError, match="not found"):
            supervisor.load_and_validate_think_result(tmp_path / "nonexistent.json")

    def test_missing_fields_raises(self, tmp_path: Path):
        path = tmp_path / "think_result.json"
        path.write_text(json.dumps({"spec": "test"}))

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        with pytest.raises(ValueError, match="missing fields"):
            supervisor.load_and_validate_think_result(path)

    def test_empty_stages_raises(self, tmp_path: Path):
        tr = make_think_result()
        d = tr.to_dict()
        d["stages"] = []
        path = tmp_path / "think_result.json"
        path.write_text(json.dumps(d))

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        with pytest.raises(ValueError, match="no stages"):
            supervisor.load_and_validate_think_result(path)

    def test_stage_missing_goal_raises(self, tmp_path: Path):
        tr = make_think_result()
        d = tr.to_dict()
        del d["stages"][0]["goal"]
        path = tmp_path / "think_result.json"
        path.write_text(json.dumps(d))

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        with pytest.raises(ValueError, match="missing 'goal'"):
            supervisor.load_and_validate_think_result(path)

    def test_stage_missing_sub_tasks_raises(self, tmp_path: Path):
        tr = make_think_result()
        d = tr.to_dict()
        d["stages"][0]["sub_tasks"] = []
        path = tmp_path / "think_result.json"
        path.write_text(json.dumps(d))

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        with pytest.raises(ValueError, match="no sub_tasks"):
            supervisor.load_and_validate_think_result(path)


class TestBuildBriefing:
    def test_briefing_contains_stage_info(self):
        tr = make_think_result()
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = ["pytest"]

        briefing = supervisor.build_briefing(
            stage=tr.stages[0],
            completed_stages=[],
            think_result=tr,
        )
        assert briefing.overall_goal == "Test spec"
        assert briefing.current_stage.stage_id == 1
        assert briefing.verification_commands == ["pytest"]
        assert briefing.previous_summaries == []

    def test_briefing_includes_previous_summaries(self):
        tr = make_think_result()
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = ["pytest"]

        completed = [{"stage_id": 1, "summary": "Stage 1 done"}]
        briefing = supervisor.build_briefing(
            stage=tr.stages[1],
            completed_stages=completed,
            think_result=tr,
        )
        assert briefing.previous_summaries == ["Stage 1 done"]


class TestShouldPlanNext:
    def test_returns_true_when_all_stages_completed(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        stages = [make_think_result().stages[0]]
        completed = [{"stage_id": 1, "summary": "done"}]
        assert supervisor.should_plan_next(stages, completed) is True

    def test_returns_false_when_stages_remain(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        stages = make_think_result().stages  # 2 stages
        completed = [{"stage_id": 1, "summary": "done"}]
        assert supervisor.should_plan_next(stages, completed) is False


class TestCollectChangedFiles:
    def test_collects_from_multiple_results(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        results = [
            MagicMock(changed_files=["src/a.py", "src/b.py"]),
            MagicMock(changed_files=["src/b.py", "src/c.py"]),
        ]
        files = supervisor.collect_changed_files(results)
        assert set(files) == {"src/a.py", "src/b.py", "src/c.py"}

    def test_empty_results(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        assert supervisor.collect_changed_files([]) == []


class TestBuildChallengeMessage:
    def test_builds_message_with_files(self):
        from codex_claude_orchestrator.v4.long_task_models import ChallengeTarget

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        target = ChallengeTarget(
            worker_id="backend-1",
            challenge_message="API 路径应该是 /api/auth/login",
            affected_files=["src/api/auth.py"],
        )
        msg = supervisor.build_challenge_message(target)
        assert "API 路径应该是 /api/auth/login" in msg
        assert "src/api/auth.py" in msg

    def test_builds_message_without_files(self):
        from codex_claude_orchestrator.v4.long_task_models import ChallengeTarget

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        target = ChallengeTarget(
            worker_id="backend-1",
            challenge_message="缺少 rate limiting",
        )
        msg = supervisor.build_challenge_message(target)
        assert "缺少 rate limiting" in msg
        assert "未指定" in msg


class TestParseReviewVerdict:
    def test_parses_json_block(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        output = '''
Some text before

```json
{
  "verdict": "OK",
  "checklist": [],
  "quality_notes": [],
  "risks": [],
  "suggestions": [],
  "contract_compliance": [],
  "cross_worker_issues": [],
  "action": "pass",
  "stage_summary": "All good"
}
```

Some text after
'''
        rv = supervisor.parse_review_verdict(output)
        assert rv.action == "pass"
        assert rv.verdict == "OK"
        assert rv.stage_summary == "All good"

    def test_parses_raw_json(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        output = json.dumps({
            "verdict": "WARN",
            "checklist": [],
            "quality_notes": [],
            "risks": [],
            "suggestions": [],
            "contract_compliance": [],
            "cross_worker_issues": [],
            "action": "challenge",
            "challenge_targets": [
                {"worker_id": "w1", "challenge_message": "fix this", "affected_files": []}
            ],
            "stage_summary": "Needs work",
        })
        rv = supervisor.parse_review_verdict(output)
        assert rv.action == "challenge"
        assert rv.challenge_targets is not None
        assert rv.challenge_targets[0].worker_id == "w1"

    def test_invalid_json_raises(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        with pytest.raises(ValueError, match="Failed to parse"):
            supervisor.parse_review_verdict("not json at all")


class TestParsePlanAdversaryVerdict:
    def test_parses_pass(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        output = json.dumps({
            "verdict": "pass",
            "issues": [],
            "auto_fixes": [],
            "summary": "OK",
        })
        pv = supervisor.parse_plan_adversary_verdict(output)
        assert pv.verdict == "pass"

    def test_parses_fix_with_issues(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        output = json.dumps({
            "verdict": "fix",
            "issues": [
                {"category": "contract", "severity": "warn", "location": "stages[0]",
                 "description": "Missing response_body", "suggestion": "Add it"}
            ],
            "auto_fixes": [
                {"location": "stages[0].response_body", "current_value": None,
                 "suggested_value": {"token": "str"}, "reason": "Required"}
            ],
            "summary": "1 issue",
        })
        pv = supervisor.parse_plan_adversary_verdict(output)
        assert pv.verdict == "fix"
        assert len(pv.issues) == 1
        assert pv.issues[0].category == "contract"
        assert len(pv.auto_fixes) == 1


class TestReplanRemainingStages:
    def test_calls_plan_next_stage(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.plan_next_stage = MagicMock(return_value=StagePlan(
            stage_id=99, goal="replanned", acceptance_criteria=[], contract=Contract(),
            sub_tasks=[], dependencies=[],
        ))

        result = supervisor.replan_remaining_stages(
            current_stage=make_think_result().stages[0],
            completed_stages=[],
            think_result=make_think_result(),
            reason="need to replan",
        )
        assert result.stage_id == 99
        supervisor.plan_next_stage.assert_called_once()


class TestReadWorkerOutbox:
    def test_reads_from_event_store(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        store = FakeEventStore()
        supervisor.event_store = store

        store.append(
            stream_id="worker-1",
            type="turn.completed",
            crew_id="c1",
            worker_id="worker-1",
            payload={"output": "implementation done", "changed_files": ["src/a.py"]},
        )

        result = supervisor._read_worker_outbox("worker-1")
        assert result["output"] == "implementation done"
        assert result["changed_files"] == ["src/a.py"]

    def test_reads_artifact_written_if_no_turn_completed(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        store = FakeEventStore()
        supervisor.event_store = store

        store.append(
            stream_id="worker-1",
            type="artifact.written",
            crew_id="c1",
            worker_id="worker-1",
            payload={"content": "some output"},
        )

        result = supervisor._read_worker_outbox("worker-1")
        assert result["content"] == "some output"

    def test_raises_when_no_events(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.event_store = FakeEventStore()

        with pytest.raises(ValueError, match="no output found"):
            supervisor._read_worker_outbox("nonexistent")


class TestRunFinalVerification:
    def test_runs_all_commands(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = ["echo ok", "echo done"]
        supervisor.repo_root = Path("/tmp")
        supervisor.event_store = FakeEventStore()
        supervisor._crew_id = "c1"

        supervisor._run_final_verification()

        events = supervisor.event_store.events
        verification_events = [e for e in events if e["type"].startswith("verification")]
        assert len(verification_events) == 2
        assert all(e["type"] == "verification.passed" for e in verification_events)

    def test_records_failure_on_nonzero_exit(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = ["false"]
        supervisor.repo_root = Path("/tmp")
        supervisor.event_store = FakeEventStore()
        supervisor._crew_id = "c1"

        supervisor._run_final_verification()

        events = supervisor.event_store.events
        failed = [e for e in events if e["type"] == "verification.failed"]
        assert len(failed) == 1

    def test_empty_commands_does_nothing(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = []
        supervisor.repo_root = Path("/tmp")
        supervisor.event_store = FakeEventStore()
        supervisor._crew_id = "c1"

        supervisor._run_final_verification()
        assert len(supervisor.event_store.events) == 0


class TestAccept:
    def test_calls_controller_accept(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.controller = MagicMock()
        supervisor.controller.accept.return_value = {"status": "accepted"}
        supervisor._crew_id = "c1"
        supervisor.goal = "refactor auth"

        supervisor._accept()

        supervisor.controller.accept.assert_called_once()
        call_kwargs = supervisor.controller.accept.call_args[1]
        assert call_kwargs["crew_id"] == "c1"
        assert "refactor auth" in call_kwargs["summary"]


class TestGetActiveTurns:
    def test_returns_turns_from_supervisor(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        mock_supervisor = MagicMock()
        mock_supervisor.get_active_turns.return_value = {
            "worker-1": {"turn_id": "t1", "status": "running"},
        }
        supervisor.supervisor = mock_supervisor
        supervisor._crew_id = "c1"

        stage = make_think_result().stages[0]
        result = supervisor.get_active_turns(stage)

        assert "worker-1" in result
        mock_supervisor.get_active_turns.assert_called_once_with(crew_id="c1")

    def test_returns_empty_on_no_method(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        mock_supervisor = MagicMock(spec=[])
        supervisor.supervisor = mock_supervisor
        supervisor._crew_id = "c1"

        stage = make_think_result().stages[0]
        result = supervisor.get_active_turns(stage)
        assert result == {}


class TestMergeStageResults:
    def test_skips_failed_results(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.repo_root = Path("/tmp/test")
        supervisor._crew_id = "c1"
        supervisor.controller = MagicMock()

        results = [MagicMock(success=False)]

        # Should not raise
        supervisor.merge_stage_results(make_think_result().stages[0], results)

    def test_empty_results_does_nothing(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.repo_root = Path("/tmp/test")
        supervisor._crew_id = "c1"
        supervisor.controller = MagicMock()

        supervisor.merge_stage_results(make_think_result().stages[0], [])


class TestSpawnSubAgentNoMock:
    def test_creates_real_worker_contract(self):
        """_spawn_sub_agent must use WorkerContract, not MagicMock."""
        import ast
        import inspect
        import textwrap

        source = inspect.getsource(LongTaskSupervisor._spawn_sub_agent)
        source = textwrap.dedent(source)
        tree = ast.parse(source)

        # Walk AST looking for MagicMock usage
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "MagicMock":
                pytest.fail("_spawn_sub_agent still uses MagicMock in production code")
            if isinstance(node, ast.Attribute) and node.attr == "MagicMock":
                pytest.fail("_spawn_sub_agent still uses MagicMock in production code")
