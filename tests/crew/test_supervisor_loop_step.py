from unittest.mock import MagicMock, patch

from codex_claude_orchestrator.crew.loop_step_result import LoopStepResult
from codex_claude_orchestrator.crew.supervisor_loop import CrewSupervisorLoop


def test_run_step_returns_waiting_when_no_workers_done():
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "busy", "role": "implementer"}],
        "blackboard": [],
        "decisions": [],
        "messages": [],
    }
    loop = CrewSupervisorLoop(controller=controller)
    with patch.object(loop, "_poll_workers", return_value={"all_done": False}):
        result = loop.run_step("c1", verification_commands=["pytest"])
    assert isinstance(result, LoopStepResult)
    assert result.action == "waiting"


def test_run_step_returns_ready_for_accept_when_verify_passes():
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [],
        "decisions": [],
        "messages": [],
    }
    loop = CrewSupervisorLoop(controller=controller)
    with patch.object(loop, "_poll_workers", return_value={"all_done": True}), \
         patch.object(loop, "_auto_verify", return_value={"passed": True, "failure_count": 0}):
        result = loop.run_step("c1", verification_commands=["pytest"])
    assert result.action == "ready_for_accept"


def test_run_step_returns_needs_decision_after_3_failures():
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [],
        "decisions": [],
        "messages": [],
    }
    loop = CrewSupervisorLoop(controller=controller)
    with patch.object(loop, "_poll_workers", return_value={"all_done": True}), \
         patch.object(loop, "_auto_verify", return_value={"passed": False, "failure_count": 3, "summary": "pytest failed"}):
        result = loop.run_step("c1", verification_commands=["pytest"])
    assert result.action == "needs_decision"
    assert "3" in result.reason
