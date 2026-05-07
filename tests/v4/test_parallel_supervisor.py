"""Tests for the ParallelSupervisor two-layer adversarial review system."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from codex_claude_orchestrator.v4.parallel_supervisor import ParallelSupervisor
from codex_claude_orchestrator.v4.subtask import SubTask


def _make_subtask(task_id: str, description: str = "", scope: list[str] | None = None) -> SubTask:
    return SubTask(
        task_id=task_id,
        description=description or f"Task {task_id}",
        scope=scope or ["src/"],
    )


def _make_controller(*, changes_map: dict[str, dict] | None = None, verify_results: list[dict] | None = None):
    """Build a mock controller with configurable changes/verify/challenge/ensure_worker."""
    controller = MagicMock()
    default_worker_idx = [0]

    def ensure_worker_side_effect(*, repo_root, crew_id, contract, allow_dirty_base=False):
        wid = f"worker-{contract.contract_id}"
        return {"worker_id": wid, "contract_id": contract.contract_id}

    controller.ensure_worker = MagicMock(side_effect=ensure_worker_side_effect)

    # changes: return per-worker or default
    if changes_map is not None:
        def changes_side_effect(*, crew_id, worker_id=None):
            return changes_map.get(worker_id, {"changed_files": [], "worker_id": worker_id})
        controller.changes = MagicMock(side_effect=changes_side_effect)
    else:
        controller.changes = MagicMock(return_value={"changed_files": ["src/app.py"], "worker_id": "worker-x"})

    # verify
    verify_iter = iter(verify_results or [{"passed": True, "summary": "ok"}])
    controller.verify = MagicMock(side_effect=lambda **kw: next(verify_iter))

    controller.challenge = MagicMock(return_value={"crew_id": "crew-1", "summary": "", "task_id": None})

    return controller


def _make_supervisor(*, turn_results: list[dict] | None = None):
    """Build a mock supervisor with async_run_worker_turn."""
    supervisor = MagicMock()
    results = list(turn_results or [{"status": "turn_completed", "turn_id": "turn-1"}])
    supervisor.async_run_worker_turn = AsyncMock(side_effect=lambda **kw: results.pop(0))
    return supervisor


def _make_event_store(*, events_by_turn: dict[str, list[dict]] | None = None):
    """Build a mock event store with configurable list_by_turn results."""
    store = MagicMock()
    turn_events = events_by_turn or {}

    def list_by_turn(turn_id):
        raw_events = turn_events.get(turn_id, [])
        mock_events = []
        for ev in raw_events:
            me = MagicMock()
            me.type = ev.get("type", "")
            me.payload = ev.get("payload", {})
            me.worker_id = ev.get("worker_id", "")
            mock_events.append(me)
        return mock_events

    store.list_by_turn = MagicMock(side_effect=list_by_turn)
    return store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_watch_and_review_all_pass(tmp_path: Path) -> None:
    """All workers complete and pass unit review."""
    changes_map = {
        "worker-source-task-1": {"changed_files": ["src/a.py"], "worker_id": "worker-source-task-1"},
        "worker-source-task-2": {"changed_files": ["src/b.py"], "worker_id": "worker-source-task-2"},
    }
    controller = _make_controller(
        changes_map=changes_map,
        verify_results=[{"passed": True, "summary": "ok"}],
    )
    supervisor = _make_supervisor(
        turn_results=[
            # source turns (round 1)
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-source-task-1-source"},
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-source-task-2-source"},
            # unit review turns (round 1)
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-review-task-1-unit_review"},
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-review-task-2-unit_review"},
        ]
    )
    event_store = _make_event_store(events_by_turn={
        "parallel-round-1-worker-review-task-1-unit_review": [],
        "parallel-round-1-worker-review-task-2-unit_review": [],
    })

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    subtasks = [_make_subtask("task-1"), _make_subtask("task-2")]

    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=subtasks,
        verification_commands=["pytest -q"],
        max_rounds=2,
    )

    assert result["status"] == "ready_for_codex_accept"
    assert result["runtime"] == "v4-parallel"
    assert subtasks[0].status == "passed"
    assert subtasks[1].status == "passed"
    # verify was called for integration
    controller.verify.assert_called_once()


@pytest.mark.asyncio
async def test_parallel_watch_and_review_failure(tmp_path: Path) -> None:
    """Unit review failure marks subtask as failed and triggers challenge."""
    changes_map = {
        "worker-source-task-1": {"changed_files": ["src/a.py"], "worker_id": "worker-source-task-1"},
    }
    controller = _make_controller(
        changes_map=changes_map,
        verify_results=[{"passed": True, "summary": "ok"}],
    )
    supervisor = _make_supervisor(
        turn_results=[
            # source turn (round 1)
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-source-task-1-source"},
            # unit review turn (round 1)
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-review-task-1-unit_review"},
        ]
    )

    # Create a mock event with BLOCK in the summary
    block_event = MagicMock()
    block_event.type = "worker.outbox.detected"
    block_event.payload = {"summary": "BLOCK: code quality issues found"}
    block_event.worker_id = "worker-review-task-1"

    event_store = MagicMock()
    event_store.list_by_turn = MagicMock(return_value=[block_event])

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    subtasks = [_make_subtask("task-1")]

    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=subtasks,
        verification_commands=["pytest -q"],
        max_rounds=1,
    )

    assert result["status"] == "max_rounds_exhausted"
    assert subtasks[0].status == "failed"
    controller.challenge.assert_called()
    challenge_args = controller.challenge.call_args_list
    assert any("unit_review_fail" in str(call) for call in challenge_args)


@pytest.mark.asyncio
async def test_integration_review_detects_conflicts(tmp_path: Path) -> None:
    """Same file changed by multiple workers = conflict."""
    changes_map = {
        "worker-source-task-1": {"changed_files": ["src/shared.py"], "worker_id": "worker-source-task-1"},
        "worker-source-task-2": {"changed_files": ["src/shared.py"], "worker_id": "worker-source-task-2"},
    }
    controller = _make_controller(
        changes_map=changes_map,
        verify_results=[{"passed": True, "summary": "ok"}],
    )
    supervisor = _make_supervisor(
        turn_results=[
            # source turns (round 1)
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-source-task-1-source"},
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-source-task-2-source"},
            # unit review turns (round 1)
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-review-task-1-unit_review"},
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-review-task-2-unit_review"},
        ]
    )
    event_store = _make_event_store(events_by_turn={
        "parallel-round-1-worker-review-task-1-unit_review": [],
        "parallel-round-1-worker-review-task-2-unit_review": [],
    })

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    subtasks = [_make_subtask("task-1"), _make_subtask("task-2")]

    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=subtasks,
        verification_commands=["pytest -q"],
        max_rounds=1,
    )

    assert result["status"] == "max_rounds_exhausted"
    # Should have attempted challenge for conflict
    controller.challenge.assert_called()
    challenge_calls = controller.challenge.call_args_list
    assert any("integration_conflict" in str(call) for call in challenge_calls)


@pytest.mark.asyncio
async def test_integration_review_passes_no_conflicts(tmp_path: Path) -> None:
    """No conflicts and tests pass = integration pass."""
    changes_map = {
        "worker-source-task-1": {"changed_files": ["src/a.py"], "worker_id": "worker-source-task-1"},
        "worker-source-task-2": {"changed_files": ["src/b.py"], "worker_id": "worker-source-task-2"},
    }
    controller = _make_controller(
        changes_map=changes_map,
        verify_results=[{"passed": True, "summary": "all tests passed"}],
    )
    supervisor = _make_supervisor(
        turn_results=[
            # source turns (round 1)
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-source-task-1-source"},
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-source-task-2-source"},
            # unit review turns (round 1)
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-review-task-1-unit_review"},
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-review-task-2-unit_review"},
        ]
    )
    event_store = _make_event_store(events_by_turn={
        "parallel-round-1-worker-review-task-1-unit_review": [],
        "parallel-round-1-worker-review-task-2-unit_review": [],
    })

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    subtasks = [_make_subtask("task-1"), _make_subtask("task-2")]

    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=subtasks,
        verification_commands=["pytest -q"],
        max_rounds=2,
    )

    assert result["status"] == "ready_for_codex_accept"
    assert result["runtime"] == "v4-parallel"
    assert subtasks[0].status == "passed"
    assert subtasks[1].status == "passed"


@pytest.mark.asyncio
async def test_detect_conflicts(tmp_path: Path) -> None:
    """_detect_conflicts finds files changed by multiple workers."""
    ps = ParallelSupervisor(
        controller=MagicMock(),
        supervisor=MagicMock(),
        event_store=MagicMock(),
    )
    all_changes = [
        {"worker_id": "w1", "changed_files": ["src/a.py", "src/b.py"]},
        {"worker_id": "w2", "changed_files": ["src/b.py", "src/c.py"]},
    ]
    conflicts = ps._detect_conflicts(all_changes)
    assert "src/b.py" in conflicts
    assert set(conflicts["src/b.py"]) == {"w1", "w2"}
    assert "src/a.py" not in conflicts
    assert "src/c.py" not in conflicts


@pytest.mark.asyncio
async def test_cancel_event_stops_supervision(tmp_path: Path) -> None:
    """Setting cancel_event before supervise starts should return cancelled immediately."""
    controller = _make_controller()
    supervisor = _make_supervisor()
    event_store = _make_event_store()

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    cancel = threading.Event()
    cancel.set()  # Pre-cancel

    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=[_make_subtask("task-1")],
        verification_commands=["pytest -q"],
        cancel_event=cancel,
    )

    assert result["status"] == "cancelled"


@pytest.mark.asyncio
async def test_verification_failure_challenges_and_retries(tmp_path: Path) -> None:
    """When verification fails, integration challenges and retries next round."""
    changes_map = {
        "worker-source-task-1": {"changed_files": ["src/a.py"], "worker_id": "worker-source-task-1"},
    }
    controller = _make_controller(
        changes_map=changes_map,
        verify_results=[
            {"passed": False, "summary": "test failure"},
            {"passed": True, "summary": "ok"},
        ],
    )
    supervisor = _make_supervisor(
        turn_results=[
            # Round 1: source + unit review
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-source-task-1-source"},
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-review-task-1-unit_review"},
            # Round 2: source + unit review
            {"status": "turn_completed", "turn_id": "parallel-round-2-worker-source-task-1-source"},
            {"status": "turn_completed", "turn_id": "parallel-round-2-worker-review-task-1-unit_review"},
        ]
    )
    event_store = _make_event_store(events_by_turn={
        "parallel-round-1-worker-review-task-1-unit_review": [],
        "parallel-round-2-worker-review-task-1-unit_review": [],
    })

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    subtasks = [_make_subtask("task-1")]

    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=subtasks,
        verification_commands=["pytest -q"],
        max_rounds=2,
    )

    assert result["status"] == "ready_for_codex_accept"
    assert result["rounds"] == 2


@pytest.mark.asyncio
async def test_unit_review_block_verdict_detected(tmp_path: Path) -> None:
    """Unit review must detect BLOCK verdict from event store using the correct turn_id.

    The bug: when async_run_worker_turn does NOT return a turn_id, the fallback
    constructs it from task_id, but events are stored keyed by worker_id.
    This causes the event store query to return empty, defaulting verdict to pass.
    """
    changes_map = {
        "worker-source-task-1": {"changed_files": ["src/a.py"], "worker_id": "worker-source-task-1"},
    }
    controller = _make_controller(changes_map=changes_map)

    # async_run_worker_turn does NOT return turn_id in the review result,
    # forcing the fallback path in _run_unit_review
    supervisor = _make_supervisor(turn_results=[
        {"status": "turn_completed", "turn_id": "source-turn-1"},
        {"status": "turn_completed"},  # no turn_id — triggers fallback
    ])

    # Event store keyed by the worker_id-based turn_id (the correct key)
    # The fallback would use task_id-based key which won't match
    event_store = _make_event_store(events_by_turn={
        "parallel-round-1-worker-review-task-1-unit_review": [
            {"type": "worker.outbox.detected", "payload": {"summary": "BLOCK: critical security issue"}, "worker_id": "worker-review-task-1"},
        ],
    })

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    subtasks = [_make_subtask("task-1")]

    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=subtasks,
        verification_commands=["pytest -q"],
        max_rounds=1,
    )

    # Must NOT be ready — BLOCK should have been detected
    assert result["status"] == "max_rounds_exhausted"
    assert subtasks[0].status == "failed"


@pytest.mark.asyncio
async def test_progress_callback_invoked(tmp_path: Path) -> None:
    """Progress callback should be called with watching and integration phases."""
    changes_map = {
        "worker-source-task-1": {"changed_files": ["src/a.py"], "worker_id": "worker-source-task-1"},
    }
    controller = _make_controller(
        changes_map=changes_map,
        verify_results=[{"passed": True, "summary": "ok"}],
    )
    supervisor = _make_supervisor(
        turn_results=[
            # source turn (round 1)
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-source-task-1-source"},
            # unit review turn (round 1)
            {"status": "turn_completed", "turn_id": "parallel-round-1-worker-review-task-1-unit_review"},
        ]
    )
    event_store = _make_event_store(events_by_turn={"parallel-round-1-worker-review-task-1-unit_review": []})

    phases: list[tuple[str, int, int]] = []

    def on_progress(phase: str, round_idx: int, max_rounds: int) -> None:
        phases.append((phase, round_idx, max_rounds))

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=[_make_subtask("task-1")],
        verification_commands=["pytest -q"],
        max_rounds=1,
        progress_callback=on_progress,
    )

    assert result["status"] == "ready_for_codex_accept"
    phase_names = [p[0] for p in phases]
    assert "watching" in phase_names
    assert "integration" in phase_names


@pytest.mark.asyncio
async def test_max_workers_limits_concurrency(tmp_path: Path) -> None:
    """max_workers should limit how many workers run concurrently."""
    import asyncio

    concurrency = 0
    max_concurrency = 0

    changes_map = {
        f"worker-source-task-{i}": {"changed_files": [f"src/f{i}.py"], "worker_id": f"worker-source-task-{i}"}
        for i in range(1, 5)
    }
    controller = _make_controller(changes_map=changes_map)

    async def tracking_run_worker_turn(*, cancel_event=None, **kwargs):
        nonlocal concurrency, max_concurrency
        concurrency += 1
        max_concurrency = max(max_concurrency, concurrency)
        await asyncio.sleep(0.05)  # Simulate work
        concurrency -= 1
        return {"status": "turn_completed", "turn_id": f"turn-{kwargs.get('worker_id', 'x')}"}

    supervisor = MagicMock()
    supervisor.async_run_worker_turn = AsyncMock(side_effect=tracking_run_worker_turn)

    event_store = _make_event_store()

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    subtasks = [_make_subtask(f"task-{i}") for i in range(1, 5)]

    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=subtasks,
        verification_commands=["pytest -q"],
        max_rounds=1,
        max_workers=2,
    )

    assert max_concurrency <= 2, f"Expected max 2 concurrent workers, got {max_concurrency}"


@pytest.mark.asyncio
async def test_worker_cleanup_on_turn_failure(tmp_path: Path) -> None:
    """Workers should be stopped when turns fail."""
    changes_map = {
        "worker-source-task-1": {"changed_files": ["src/a.py"], "worker_id": "worker-source-task-1"},
    }
    controller = _make_controller(changes_map=changes_map)
    # Source turn fails
    supervisor = _make_supervisor(turn_results=[
        {"status": "turn_failed", "turn_id": "t1", "reason": "worker crashed"},
    ])
    event_store = _make_event_store()

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    subtasks = [_make_subtask("task-1")]

    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=subtasks,
        verification_commands=["pytest -q"],
        max_rounds=1,
    )

    assert result["status"] == "max_rounds_exhausted"
    # Controller should have been asked to release the worker
    controller.release_worker.assert_called()


@pytest.mark.asyncio
async def test_integration_review_respects_cancel(tmp_path: Path) -> None:
    """Integration review should check cancel_event between verification commands."""
    changes_map = {
        "worker-source-task-1": {"changed_files": ["src/a.py"], "worker_id": "worker-source-task-1"},
    }
    cancel = threading.Event()

    verify_calls = []

    def verify_side_effect(**kwargs):
        verify_calls.append(1)
        if len(verify_calls) == 1:
            cancel.set()  # Cancel after first verification
        return {"passed": True, "summary": "ok"}

    controller = _make_controller(changes_map=changes_map)
    controller.verify = MagicMock(side_effect=verify_side_effect)

    supervisor = _make_supervisor(turn_results=[
        {"status": "turn_completed", "turn_id": "t1"},
        {"status": "turn_completed", "turn_id": "review-t1"},
    ])
    event_store = _make_event_store(events_by_turn={"review-t1": []})

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    subtasks = [_make_subtask("task-1")]

    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=subtasks,
        verification_commands=["pytest -q", "mypy src/"],  # 2 commands
        max_rounds=1,
        cancel_event=cancel,
    )

    # Should have been cancelled before running second verification
    assert result["status"] == "cancelled"
    assert len(verify_calls) == 1  # Only first command ran


@pytest.mark.asyncio
async def test_per_worker_timeout(tmp_path: Path) -> None:
    """A slow worker should be timed out without blocking other workers."""
    import asyncio

    changes_map = {
        "worker-source-task-1": {"changed_files": ["src/a.py"], "worker_id": "worker-source-task-1"},
        "worker-source-task-2": {"changed_files": ["src/b.py"], "worker_id": "worker-source-task-2"},
    }
    controller = _make_controller(changes_map=changes_map)

    call_count = [0]

    async def slow_then_fast(*, cancel_event=None, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            await asyncio.sleep(10)  # First call hangs
            return {"status": "turn_completed", "turn_id": "t1"}
        return {"status": "turn_completed", "turn_id": "t2"}

    supervisor = MagicMock()
    supervisor.async_run_worker_turn = AsyncMock(side_effect=slow_then_fast)
    event_store = _make_event_store()

    ps = ParallelSupervisor(controller=controller, supervisor=supervisor, event_store=event_store)
    subtasks = [_make_subtask("task-1"), _make_subtask("task-2")]

    result = await ps.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        goal="Build feature X",
        subtasks=subtasks,
        verification_commands=["pytest -q"],
        max_rounds=1,
        worker_timeout=0.5,
    )

    # Task-1 should have timed out, task-2 should have succeeded
    # At least one subtask should be failed (the timed-out one)
    statuses = [st.status for st in subtasks]
    assert "failed" in statuses
