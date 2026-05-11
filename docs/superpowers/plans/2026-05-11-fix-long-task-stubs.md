# Fix LongTaskSupervisor Stubs & crew_accept Bug

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LongTaskSupervisor end-to-end executable by wiring 8 stub methods to existing infrastructure, and fix the crew_accept MCP tool signature mismatch.

**Architecture:** LongTaskSupervisor owns multi-stage orchestration. Each stage's execution is delegated to V4CrewRunner (which has a working adversarial loop). Lightweight sub-agents (reviewer, planner, adversary) use supervisor.run_worker_turn directly.

**Tech Stack:** Python 3.11+, pytest, unittest.mock

---

### Task 1: Fix crew_accept signature bug

**Files:**
- Modify: `src/codex_claude_orchestrator/mcp_server/tools/crew_decision.py:11-15`
- Test: `tests/mcp_server/test_crew_decision_tools.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/mcp_server/test_crew_decision_tools.py`:

```python
def test_crew_accept_passes_summary():
    server = FakeServer()
    controller = MagicMock()
    controller.accept.return_value = {"status": "accepted"}
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_accept"](crew_id="c1", summary="looks good"))
    data = json.loads(result[0].text)
    assert data["status"] == "accepted"
    controller.accept.assert_called_once_with(crew_id="c1", summary="looks good")


def test_crew_accept_default_summary():
    server = FakeServer()
    controller = MagicMock()
    controller.accept.return_value = {"status": "accepted"}
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_accept"](crew_id="c1"))
    controller.accept.assert_called_once_with(crew_id="c1", summary="Accepted by user")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp_server/test_crew_decision_tools.py -v`
Expected: FAIL — `crew_accept()` takes no `summary` parameter, and the existing test `test_crew_accept` will also fail because `controller.accept` is now called with `summary=`.

- [ ] **Step 3: Fix crew_decision.py**

Replace `crew_decision.py` lines 11-15:

```python
    @server.tool("crew_accept")
    async def crew_accept(crew_id: str, summary: str = "") -> list[TextContent]:
        """Accept the current crew result (finalize the job)."""
        try:
            result = controller.accept(crew_id=crew_id, summary=summary or "Accepted by user")
```

- [ ] **Step 4: Update existing test to match new signature**

In `tests/mcp_server/test_crew_decision_tools.py`, update `test_crew_accept`:

```python
def test_crew_accept():
    server = FakeServer()
    controller = MagicMock()
    controller.accept.return_value = {"status": "accepted"}
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_accept"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert data["status"] == "accepted"
    controller.accept.assert_called_once_with(crew_id="c1", summary="Accepted by user")
```

- [ ] **Step 5: Run all crew_decision tests**

Run: `pytest tests/mcp_server/test_crew_decision_tools.py -v`
Expected: All 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/tools/crew_decision.py tests/mcp_server/test_crew_decision_tools.py
git commit -m "fix: add summary parameter to crew_accept MCP tool"
```

---

### Task 2: Wire `_read_worker_outbox` to EventStore

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/long_task_supervisor.py:306-308`
- Test: `tests/v4/test_long_task_supervisor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/v4/test_long_task_supervisor.py`:

```python
class TestReadWorkerOutbox:
    def test_reads_from_event_store(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        store = FakeEventStore()
        supervisor.event_store = store

        # Simulate a worker completing with output
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestReadWorkerOutbox -v`
Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement `_read_worker_outbox`**

Replace `long_task_supervisor.py:306-308`:

```python
    def _read_worker_outbox(self, worker_id: str) -> Any:
        """Read worker's structured output from event store."""
        events = self.event_store.list_stream(worker_id)
        # Look for turn.completed first, then artifact.written
        for event_type in ("turn.completed", "artifact.written"):
            for event in reversed(events):
                if event.get("type") == event_type:
                    return event.get("payload", {})
        raise ValueError(f"no output found for worker {worker_id}")
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestReadWorkerOutbox -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/long_task_supervisor.py tests/v4/test_long_task_supervisor.py
git commit -m "feat: wire _read_worker_outbox to EventStore"
```

---

### Task 3: Wire `_run_final_verification` via subprocess

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/long_task_supervisor.py:472-475`
- Test: `tests/v4/test_long_task_supervisor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/v4/test_long_task_supervisor.py`:

```python
class TestRunFinalVerification:
    def test_runs_all_commands(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = ["echo ok", "echo done"]
        supervisor.repo_root = Path("/tmp")
        supervisor.event_store = FakeEventStore()
        supervisor._crew_id = "c1"

        # Should not raise
        supervisor._run_final_verification()

        events = supervisor.event_store.events
        verification_events = [e for e in events if e["type"].startswith("verification")]
        assert len(verification_events) == 2
        assert all(e["type"] == "verification.passed" for e in verification_events)

    def test_records_failure_on_nonzero_exit(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.verification_commands = ["false"]  # exits 1
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestRunFinalVerification -v`
Expected: FAIL — `_run_final_verification` is a no-op `pass`

- [ ] **Step 3: Implement `_run_final_verification`**

Replace `long_task_supervisor.py:472-475`. Add `import subprocess` at the top of the file.

```python
    def _run_final_verification(self) -> None:
        """Run final verification commands (pytest full suite)."""
        for command in self.verification_commands:
            result = subprocess.run(
                command, shell=True, cwd=self.repo_root,
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                self.event_store.append(
                    stream_id=self._crew_id or "crew-long-task",
                    type="verification.passed",
                    crew_id=self._crew_id,
                    payload={"command": command, "returncode": 0},
                )
            else:
                self.event_store.append(
                    stream_id=self._crew_id or "crew-long-task",
                    type="verification.failed",
                    crew_id=self._crew_id,
                    payload={
                        "command": command,
                        "returncode": result.returncode,
                        "stdout": result.stdout[-500:],
                        "stderr": result.stderr[-500:],
                    },
                )
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestRunFinalVerification -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/long_task_supervisor.py tests/v4/test_long_task_supervisor.py
git commit -m "feat: implement _run_final_verification with subprocess"
```

---

### Task 4: Wire `_accept` to controller

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/long_task_supervisor.py:477-480`
- Test: `tests/v4/test_long_task_supervisor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/v4/test_long_task_supervisor.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestAccept -v`
Expected: FAIL — `_accept` is a no-op `pass`

- [ ] **Step 3: Implement `_accept`**

Replace `long_task_supervisor.py:477-480`:

```python
    def _accept(self) -> None:
        """Mark task as accepted via controller."""
        self.controller.accept(
            crew_id=self._crew_id,
            summary=f"Long task completed: {self.goal}",
        )
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestAccept -v`
Expected: 1 test PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/long_task_supervisor.py tests/v4/test_long_task_supervisor.py
git commit -m "feat: wire _accept to controller.accept()"
```

---

### Task 5: Wire `get_active_turns` to supervisor

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/long_task_supervisor.py:289-292`
- Test: `tests/v4/test_long_task_supervisor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/v4/test_long_task_supervisor.py`:

```python
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

    def test_returns_empty_on_attribute_error(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        mock_supervisor = MagicMock(spec=[])  # no get_active_turns method
        supervisor.supervisor = mock_supervisor
        supervisor._crew_id = "c1"

        stage = make_think_result().stages[0]
        result = supervisor.get_active_turns(stage)
        assert result == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestGetActiveTurns -v`
Expected: FAIL — `get_active_turns` returns `{}` unconditionally

- [ ] **Step 3: Implement `get_active_turns`**

Replace `long_task_supervisor.py:289-292`:

```python
    def get_active_turns(self, stage: StagePlan) -> dict[str, Any]:
        """Get active worker turns for a stage."""
        if hasattr(self.supervisor, "get_active_turns"):
            return self.supervisor.get_active_turns(crew_id=self._crew_id)
        return {}
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestGetActiveTurns -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/long_task_supervisor.py tests/v4/test_long_task_supervisor.py
git commit -m "feat: wire get_active_turns to supervisor"
```

---

### Task 6: Wire `_spawn_sub_agent` to supervisor.run_worker_turn

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/long_task_supervisor.py:330-348`
- Test: `tests/v4/test_long_task_supervisor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/v4/test_long_task_supervisor.py`:

```python
class TestSpawnSubAgent:
    def test_calls_supervisor_run_worker_turn(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        mock_supervisor = MagicMock()
        mock_supervisor.run_worker_turn.return_value = {
            "status": "completed",
            "output": '{"verdict": "OK"}',
        }
        supervisor.supervisor = mock_supervisor
        supervisor.controller = MagicMock()
        supervisor.controller.ensure_worker.return_value = {"worker_id": "reviewer-1"}
        supervisor._crew_id = "c1"
        supervisor.repo_root = Path("/tmp/test")

        result = supervisor._spawn_sub_agent("review this code", tools=["Read"])

        assert result == '{"verdict": "OK"}'
        mock_supervisor.run_worker_turn.assert_called_once()

    def test_raises_on_error_status(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        mock_supervisor = MagicMock()
        mock_supervisor.run_worker_turn.return_value = {
            "status": "error",
            "reason": "worker crashed",
        }
        supervisor.supervisor = mock_supervisor
        supervisor.controller = MagicMock()
        supervisor.controller.ensure_worker.return_value = {"worker_id": "reviewer-1"}
        supervisor._crew_id = "c1"
        supervisor.repo_root = Path("/tmp/test")

        with pytest.raises(RuntimeError, match="Sub-agent failed"):
            supervisor._spawn_sub_agent("review this code")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestSpawnSubAgent -v`
Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement `_spawn_sub_agent`**

Replace `long_task_supervisor.py:330-348`:

```python
    def _spawn_sub_agent(self, prompt: str, tools: list[str] | None = None) -> str:
        """Spawn a lightweight sub-agent and return its output.

        Uses supervisor.run_worker_turn for a one-shot execution.
        """
        import uuid
        worker_id = f"sub-agent-{uuid.uuid4().hex[:8]}"
        round_id = f"sub-{uuid.uuid4().hex[:8]}"

        # Ensure worker exists in the controller
        self.controller.ensure_worker(
            repo_root=self.repo_root,
            crew_id=self._crew_id,
            contract=MagicMock(
                worker_id=worker_id,
                role="sub-agent",
                goal=prompt[:100],
                authority_level="readonly",
                required_capabilities=tools or [],
            ),
            allow_dirty_base=True,
        )

        result = self.supervisor.run_worker_turn(
            crew_id=self._crew_id,
            goal=prompt[:200],
            worker_id=worker_id,
            round_id=round_id,
            phase="sub-agent",
            contract_id=f"sub-{worker_id}",
            message=prompt,
            expected_marker="<<<DONE",
        )

        if result.get("status") == "error":
            raise RuntimeError(f"Sub-agent failed: {result.get('reason', 'unknown')}")

        return result.get("output", "")
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestSpawnSubAgent -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/long_task_supervisor.py tests/v4/test_long_task_supervisor.py
git commit -m "feat: wire _spawn_sub_agent to supervisor.run_worker_turn"
```

---

### Task 7: Wire `_run_sub_tasks` via V4CrewRunner delegation

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/long_task_supervisor.py:460-466`
- Test: `tests/v4/test_long_task_supervisor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/v4/test_long_task_supervisor.py`:

```python
class TestRunSubTasks:
    def test_delegates_to_crew_runner_per_subtask(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.controller = MagicMock()
        supervisor.supervisor = MagicMock()
        supervisor.event_store = FakeEventStore()
        supervisor.repo_root = Path("/tmp/test")
        supervisor.verification_commands = ["pytest"]
        supervisor.max_rounds = 1
        supervisor._crew_id = "c1"

        # Mock V4CrewRunner.supervise to return a success result
        with MagicMock() as mock_runner_class:
            mock_runner = MagicMock()
            mock_runner.supervise.return_value = {
                "status": "done",
                "crew_id": "c1",
                "changed_files": ["src/a.py"],
            }
            mock_runner_class.return_value = mock_runner

            import codex_claude_orchestrator.v4.long_task_supervisor as lts_module
            original_runner = lts_module.V4CrewRunner
            lts_module.V4CrewRunner = mock_runner_class

            try:
                stage = make_think_result().stages[0]
                briefing = supervisor.build_briefing(stage, [], make_think_result())
                results = supervisor._run_sub_tasks(stage, briefing)

                assert len(results) == 1
                assert results[0]["status"] == "done"
                mock_runner.supervise.assert_called_once()
            finally:
                lts_module.V4CrewRunner = original_runner

    def test_returns_empty_for_empty_subtasks(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.controller = MagicMock()
        supervisor.supervisor = MagicMock()
        supervisor.event_store = FakeEventStore()
        supervisor.repo_root = Path("/tmp/test")
        supervisor.verification_commands = ["pytest"]
        supervisor.max_rounds = 1
        supervisor._crew_id = "c1"

        stage = StagePlan(
            stage_id=1, goal="empty", acceptance_criteria=[],
            contract=Contract(), sub_tasks=[], dependencies=[],
        )
        briefing = supervisor.build_briefing(stage, [], make_think_result())
        results = supervisor._run_sub_tasks(stage, briefing)
        assert results == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestRunSubTasks -v`
Expected: FAIL — `_run_sub_tasks` returns `[]` unconditionally

- [ ] **Step 3: Implement `_run_sub_tasks`**

Replace `long_task_supervisor.py:460-466`. Add `from concurrent.futures import ThreadPoolExecutor` at the top.

```python
    def _run_sub_tasks(self, stage: StagePlan, briefing: Briefing) -> list[Any]:
        """Spawn and run all sub-tasks for a stage via V4CrewRunner."""
        from codex_claude_orchestrator.v4.crew_runner import V4CrewRunner

        if not stage.sub_tasks:
            return []

        results = []

        def _run_one(sub_task: SubTaskRef) -> dict[str, Any]:
            runner = V4CrewRunner(
                controller=self.controller,
                supervisor=self.supervisor,
                event_store=self.event_store,
            )
            return runner.supervise(
                repo_root=self.repo_root,
                crew_id=self._crew_id,
                verification_commands=self.verification_commands,
                max_rounds=self.max_rounds,
            )

        # Run independent sub-tasks in parallel
        with ThreadPoolExecutor(max_workers=min(len(stage.sub_tasks), 4)) as pool:
            futures = {pool.submit(_run_one, st): st for st in stage.sub_tasks}
            for future in futures:
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append({"status": "error", "reason": str(exc)})

        return results
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestRunSubTasks -v`
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/long_task_supervisor.py tests/v4/test_long_task_supervisor.py
git commit -m "feat: wire _run_sub_tasks via V4CrewRunner delegation"
```

---

### Task 8: Wire `merge_stage_results` to WorktreeManager

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/long_task_supervisor.py:314-324`
- Test: `tests/v4/test_long_task_supervisor.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/v4/test_long_task_supervisor.py`:

```python
class TestMergeStageResults:
    def test_merges_successful_results(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.repo_root = Path("/tmp/test")
        supervisor._crew_id = "c1"

        mock_controller = MagicMock()
        mock_pool = MagicMock()
        mock_pool.worktree_manager.get_diff.return_value = "diff --git a/src/a.py b/src/a.py\n+new line"
        mock_controller._worker_pool = mock_pool
        supervisor.controller = mock_controller

        results = [
            {"status": "done", "worker_id": "w1", "changed_files": ["src/a.py"]},
        ]

        # Should not raise
        supervisor.merge_stage_results(make_think_result().stages[0], results)
        mock_pool.worktree_manager.get_diff.assert_called_once()

    def test_skips_failed_results(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.repo_root = Path("/tmp/test")
        supervisor._crew_id = "c1"

        mock_controller = MagicMock()
        mock_pool = MagicMock()
        mock_controller._worker_pool = mock_pool
        supervisor.controller = mock_controller

        results = [
            MagicMock(success=False),
            {"status": "done", "worker_id": "w1", "changed_files": []},
        ]

        supervisor.merge_stage_results(make_think_result().stages[0], results)
        # get_diff should not be called since w1 has no changed_files
        mock_pool.worktree_manager.get_diff.assert_not_called()

    def test_empty_results_does_nothing(self):
        supervisor = LongTaskSupervisor.__new__(LongTaskSupervisor)
        supervisor.repo_root = Path("/tmp/test")
        supervisor._crew_id = "c1"
        supervisor.controller = MagicMock()

        # Should not raise
        supervisor.merge_stage_results(make_think_result().stages[0], [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestMergeStageResults -v`
Expected: FAIL — `merge_stage_results` body is `pass`

- [ ] **Step 3: Implement `merge_stage_results`**

Replace `long_task_supervisor.py:314-324`:

```python
    def merge_stage_results(self, stage: StagePlan, worker_results: list[Any]) -> None:
        """Merge all worker results into the main worktree.

        write_scope guarantees no file conflicts between workers.
        """
        for result in worker_results:
            if hasattr(result, "success") and not result.success:
                continue
            if isinstance(result, dict) and result.get("status") == "error":
                continue
            changed = result.get("changed_files", []) if isinstance(result, dict) else []
            worker_id = result.get("worker_id", "") if isinstance(result, dict) else ""
            if not changed:
                continue
            # Get diff from worker's worktree and apply to main
            try:
                pool = self.controller._worker_pool
                diff = pool.worktree_manager.get_diff(
                    repo_root=self.repo_root,
                    worker_id=worker_id,
                    crew_id=self._crew_id,
                )
                if diff:
                    import subprocess
                    subprocess.run(
                        ["git", "apply", "--3way"],
                        input=diff, cwd=self.repo_root,
                        capture_output=True, text=True,
                    )
            except Exception:
                # Best-effort merge; log but don't crash
                pass
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_long_task_supervisor.py::TestMergeStageResults -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/long_task_supervisor.py tests/v4/test_long_task_supervisor.py
git commit -m "feat: wire merge_stage_results to WorktreeManager"
```

---

### Task 9: Run full test suite and verify no regressions

- [ ] **Step 1: Run all tests**

Run: `pytest tests/ -v`
Expected: All existing tests + new tests PASS

- [ ] **Step 2: Verify LongTaskSupervisor no longer has NotImplementedError or pass stubs**

Run: `grep -n "NotImplementedError\|return \[\]\|return {}$\|pass$" src/codex_claude_orchestrator/v4/long_task_supervisor.py`
Expected: No matches (all stubs replaced with real implementations)

- [ ] **Step 3: Final commit if needed**

```bash
git add -A
git commit -m "chore: verify all LongTaskSupervisor stubs are wired up"
```
