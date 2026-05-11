"""Integration tests for the long task adversarial agent system.

Tests the full flow: ThinkResult validation -> briefing -> review -> challenge -> plan.
Does NOT test actual sub-agent spawning (uses mocks).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from codex_claude_orchestrator.v4.long_task_models import (
    Contract,
    ProjectContext,
    ReviewVerdict,
    StagePlan,
    SubTaskRef,
    ThinkResult,
)
from codex_claude_orchestrator.v4.long_task_supervisor import LongTaskSupervisor


# --- Fixtures ---


def _make_think_result(tmp_path: Path) -> ThinkResult:
    """Create a valid ThinkResult with 2 stages for testing."""
    return ThinkResult(
        spec="重构认证模块",
        stages=[
            StagePlan(
                stage_id=1,
                goal="探索现有认证代码",
                acceptance_criteria=["列出所有认证相关文件", "总结当前架构"],
                contract=Contract(),
                sub_tasks=[
                    SubTaskRef(task_id="1a", role="repo-context-scout", goal="探索代码")
                ],
                dependencies=[],
            ),
            StagePlan(
                stage_id=2,
                goal="实现 JWT 认证",
                acceptance_criteria=["支持 RS256", "token 过期 30 分钟", "pytest 通过"],
                contract=Contract(conventions=["use pytest"]),
                sub_tasks=[
                    SubTaskRef(
                        task_id="2a",
                        role="backend-developer",
                        goal="实现 JWT API",
                        write_scope=["src/api/auth.py"],
                    ),
                ],
                dependencies=[1],
            ),
        ],
        contract=Contract(conventions=["use pytest"]),
        project_context=ProjectContext(tech_stack=["Python 3.11", "FastAPI"]),
        acceptance_criteria=["所有认证相关代码迁移到 JWT", "pytest 全量通过"],
        open_questions=[],
    )


def _write_think_result_json(tmp_path: Path, think_result: ThinkResult | None = None) -> Path:
    """Write think_result.json to the .crew directory and return its path."""
    tr = think_result or _make_think_result(tmp_path)
    path = tmp_path / ".crew" / "think_result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tr.to_dict(), ensure_ascii=False, indent=2))
    return path


def _write_review_template(tmp_path: Path) -> Path:
    """Write a minimal review.md prompt template for testing."""
    prompt_dir = tmp_path / ".claude" / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    template = prompt_dir / "review.md"
    template.write_text(
        """Review the following stage implementation.

Overall goal: {overall_goal}
Stage goal: {stage_goal}
Acceptance criteria: {acceptance_criteria}
Contract: {contract}
Previous summaries: {previous_summaries}
Changed files: {changed_files}
Verification commands: {verification_commands}

Output a JSON verdict."""
    )
    return template


def _make_pass_verdict() -> str:
    """Return a JSON string for a passing review verdict."""
    return json.dumps({
        "verdict": "OK",
        "checklist": [{"criterion": "RS256", "status": "pass", "note": "done"}],
        "quality_notes": [],
        "risks": [],
        "suggestions": [],
        "contract_compliance": [],
        "cross_worker_issues": [],
        "action": "pass",
        "stage_summary": "完成",
    })


def _make_challenge_verdict() -> str:
    """Return a JSON string for a challenge review verdict."""
    return json.dumps({
        "verdict": "WARN",
        "checklist": [],
        "quality_notes": [],
        "risks": [],
        "suggestions": [],
        "contract_compliance": [],
        "cross_worker_issues": ["路径不一致"],
        "action": "challenge",
        "challenge_targets": [
            {
                "worker_id": "w1",
                "challenge_message": "路径应该是 /api/auth/login",
                "affected_files": [],
            }
        ],
        "stage_summary": "需要修复",
    })


def _make_supervisor(tmp_path: Path) -> LongTaskSupervisor:
    """Create a LongTaskSupervisor with mocked dependencies."""
    mock_controller = MagicMock()
    mock_supervisor = MagicMock()
    mock_event_store = MagicMock()
    mock_event_store.append.return_value = MagicMock()

    return LongTaskSupervisor(
        controller=mock_controller,
        supervisor=mock_supervisor,
        event_store=mock_event_store,
        repo_root=tmp_path,
        goal="重构认证模块",
        verification_commands=["pytest"],
        prompt_dir=tmp_path / ".claude" / "prompts",
    )


# --- Tests ---


class TestFullFlowPass:
    """Test full flow where reviewer passes on first try for all stages."""

    def test_full_flow_pass(self, tmp_path: Path):
        """Both stages pass review on first try; task completes successfully."""
        _write_think_result_json(tmp_path)
        _write_review_template(tmp_path)

        supervisor = _make_supervisor(tmp_path)

        # Mock _run_sub_tasks to return empty results (no actual workers)
        supervisor._run_sub_tasks = MagicMock(return_value=[])

        # Mock _spawn_sub_agent: stage 1 pass, stage 2 pass
        # run_reviewer calls _spawn_sub_agent once per stage
        supervisor._spawn_sub_agent = MagicMock(
            side_effect=[_make_pass_verdict(), _make_pass_verdict()]
        )

        # Mock _run_final_verification and _accept
        supervisor._run_final_verification = MagicMock()
        supervisor._accept = MagicMock()

        # plan_next_stage raises ValueError when no more stages to plan
        supervisor.plan_next_stage = MagicMock(side_effect=ValueError("no more stages"))

        result = supervisor.supervise_long_task()

        assert result["status"] == "done"
        assert result["total_stages"] >= 1
        supervisor._accept.assert_called_once()
        supervisor._run_final_verification.assert_called_once()

    def test_events_recorded(self, tmp_path: Path):
        """Verify stage.planned and stage.completed events are recorded."""
        _write_think_result_json(tmp_path)
        _write_review_template(tmp_path)

        supervisor = _make_supervisor(tmp_path)
        supervisor._run_sub_tasks = MagicMock(return_value=[])
        supervisor._spawn_sub_agent = MagicMock(
            side_effect=[_make_pass_verdict(), _make_pass_verdict()]
        )
        supervisor._run_final_verification = MagicMock()
        supervisor._accept = MagicMock()
        supervisor.plan_next_stage = MagicMock(side_effect=ValueError("no more stages"))

        supervisor.supervise_long_task()

        # Check that events were recorded
        event_calls = supervisor.event_store.append.call_args_list
        event_types = [call.kwargs.get("type") or call[1].get("type") for call in event_calls]

        # Should have stage.planned events for each stage
        assert "stage.planned" in event_types
        # Should have stage.completed events for each stage
        assert "stage.completed" in event_types


class TestFlowWithChallenge:
    """Test flow where reviewer challenges then passes."""

    def test_challenge_then_pass(self, tmp_path: Path):
        """Stage 1: challenge then pass. Stage 2: pass directly."""
        _write_think_result_json(tmp_path)
        _write_review_template(tmp_path)

        supervisor = _make_supervisor(tmp_path)
        supervisor._run_sub_tasks = MagicMock(return_value=[])

        # Mock challenge flow methods
        supervisor.challenge_parallel_workers = MagicMock()
        supervisor.get_active_turns = MagicMock(return_value={})
        supervisor.get_updated_results = MagicMock(return_value=[])

        # _spawn_sub_agent calls:
        # 1. Stage 1 first review -> challenge
        # 2. Stage 1 second review (after challenge) -> pass
        # 3. Stage 2 first review -> pass
        supervisor._spawn_sub_agent = MagicMock(
            side_effect=[
                _make_challenge_verdict(),
                _make_pass_verdict(),
                _make_pass_verdict(),
            ]
        )

        supervisor._run_final_verification = MagicMock()
        supervisor._accept = MagicMock()
        supervisor.plan_next_stage = MagicMock(side_effect=ValueError("no more stages"))

        result = supervisor.supervise_long_task()

        assert result["status"] == "done"
        supervisor.challenge_parallel_workers.assert_called_once()

    def test_challenge_sends_to_correct_worker(self, tmp_path: Path):
        """Challenge targets are forwarded to challenge_parallel_workers."""
        _write_think_result_json(tmp_path)
        _write_review_template(tmp_path)

        supervisor = _make_supervisor(tmp_path)
        supervisor._run_sub_tasks = MagicMock(return_value=[])
        supervisor.challenge_parallel_workers = MagicMock()
        supervisor.get_active_turns = MagicMock(return_value={})
        supervisor.get_updated_results = MagicMock(return_value=[])
        supervisor._spawn_sub_agent = MagicMock(
            side_effect=[
                _make_challenge_verdict(),
                _make_pass_verdict(),
                _make_pass_verdict(),
            ]
        )
        supervisor._run_final_verification = MagicMock()
        supervisor._accept = MagicMock()
        supervisor.plan_next_stage = MagicMock(side_effect=ValueError("no more stages"))

        supervisor.supervise_long_task()

        # Verify challenge was called with the correct targets
        call_args = supervisor.challenge_parallel_workers.call_args
        targets = call_args[0][0]  # first positional arg
        assert len(targets) == 1
        assert targets[0].worker_id == "w1"
        assert "路径应该是 /api/auth/login" in targets[0].challenge_message


class TestThinkResultValidationIntegration:
    """Test that invalid think_result.json prevents execution."""

    def test_invalid_think_result_no_stages(self, tmp_path: Path):
        """think_result.json with empty stages raises ValueError."""
        path = tmp_path / ".crew" / "think_result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "spec": "test",
            "stages": [],
            "contract": {},
            "project_context": {},
            "acceptance_criteria": [],
        }))

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.repo_root = tmp_path

        with pytest.raises(ValueError, match="no stages"):
            supervisor.load_and_validate_think_result(path)

    def test_invalid_think_result_missing_fields(self, tmp_path: Path):
        """think_result.json with missing required fields raises ValueError."""
        path = tmp_path / ".crew" / "think_result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"spec": "test"}))

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)

        with pytest.raises(ValueError, match="missing fields"):
            supervisor.load_and_validate_think_result(path)

    def test_missing_think_result_file(self, tmp_path: Path):
        """Non-existent think_result.json raises ValueError."""
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)

        with pytest.raises(ValueError, match="not found"):
            supervisor.load_and_validate_think_result(tmp_path / ".crew" / "think_result.json")

    def test_stage_missing_goal(self, tmp_path: Path):
        """Stage without goal raises ValueError."""
        tr = _make_think_result(tmp_path)
        d = tr.to_dict()
        del d["stages"][0]["goal"]
        path = tmp_path / ".crew" / "think_result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(d))

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)

        with pytest.raises(ValueError, match="missing 'goal'"):
            supervisor.load_and_validate_think_result(path)


class TestBriefingCarriesContext:
    """Test that briefing correctly carries context between stages."""

    def test_stage1_briefing_no_previous(self, tmp_path: Path):
        """Stage 1 briefing has no previous summaries."""
        tr = _make_think_result(tmp_path)

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = ["pytest"]

        b1 = supervisor.build_briefing(tr.stages[0], [], tr)
        assert b1.previous_summaries == []
        assert b1.overall_goal == "重构认证模块"
        assert b1.current_stage.stage_id == 1

    def test_stage2_briefing_has_previous(self, tmp_path: Path):
        """Stage 2 briefing includes stage 1 summary."""
        tr = _make_think_result(tmp_path)

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = ["pytest"]

        completed = [{"stage_id": 1, "summary": "探索完成，列出了所有文件"}]
        b2 = supervisor.build_briefing(tr.stages[1], completed, tr)
        assert b2.previous_summaries == ["探索完成，列出了所有文件"]
        assert b2.current_stage.stage_id == 2

    def test_briefing_includes_verification_commands(self, tmp_path: Path):
        """Briefing carries verification commands."""
        tr = _make_think_result(tmp_path)

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = ["pytest", "mypy src/"]

        b = supervisor.build_briefing(tr.stages[0], [], tr)
        assert b.verification_commands == ["pytest", "mypy src/"]

    def test_briefing_includes_contract(self, tmp_path: Path):
        """Briefing carries the stage contract."""
        tr = _make_think_result(tmp_path)

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = ["pytest"]

        # Stage 2 has conventions=["use pytest"]
        b = supervisor.build_briefing(tr.stages[1], [], tr)
        assert "use pytest" in b.contract.conventions

    def test_briefing_from_json_roundtrip(self, tmp_path: Path):
        """Briefing created from a JSON-loaded ThinkResult carries correct context."""
        _write_think_result_json(tmp_path)

        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = ["pytest"]

        tr = supervisor.load_and_validate_think_result(tmp_path / ".crew" / "think_result.json")

        # Stage 1 briefing
        b1 = supervisor.build_briefing(tr.stages[0], [], tr)
        assert b1.previous_summaries == []
        assert b1.overall_goal == "重构认证模块"

        # Stage 2 briefing with stage 1 completed
        completed = [{"stage_id": 1, "summary": "探索完成，列出了所有文件"}]
        b2 = supervisor.build_briefing(tr.stages[1], completed, tr)
        assert b2.previous_summaries == ["探索完成，列出了所有文件"]
        assert b2.current_stage.stage_id == 2
