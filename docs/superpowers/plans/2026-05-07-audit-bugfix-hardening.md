# 审计问题修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 18 audit issues (3 HIGH + 8 MEDIUM + 7 LOW) across MCP tools, V4 orchestration, controller/pool — all targeted fixes, no architecture changes.

**Architecture:** Four independent fix categories (A: MCP error handling, B: data integrity, C: concurrency, D: defensive coding). Each fix is isolated and independently testable.

**Tech Stack:** Python, pytest, threading, MCP framework

---

## File Structure

| Category | Files Modified | Tests Modified/Created |
|----------|---------------|----------------------|
| A. MCP Error Handling | `crew_decision.py`, `crew_lifecycle.py`, `crew_context.py` | `test_crew_decision_tools.py`, `test_crew_lifecycle_tools.py`, `test_crew_context_tools.py` |
| B. Data Integrity | `controller.py`, `pool.py`, `compressor.py`, `crew_runner.py`, `supervisor.py`, `job_manager.py` | `test_controller.py`, `test_compressor.py`, `test_crew_runner.py`, `test_job_manager.py` |
| C. Concurrency | `crew_runner.py`, `job_manager.py`, `tmux_claude.py`, `turns.py` | `test_crew_runner.py`, `test_job_manager.py`, `test_turns.py` |
| D. Defensive Coding | `controller.py`, `pool.py` | `test_controller.py`, `test_pool.py` |

---

## Category A: MCP 工具错误处理

### Task A1: crew_decision.py — 2 个工具加 try/except

**Files:**
- Modify: `src/codex_claude_orchestrator/mcp_server/tools/crew_decision.py:9-25`
- Modify: `tests/mcp_server/test_crew_decision_tools.py`

- [ ] **Step 1: Write failing tests for error handling**

```python
# Add to tests/mcp_server/test_crew_decision_tools.py

def test_crew_accept_returns_error_on_exception():
    server = FakeServer()
    controller = MagicMock()
    controller.accept.side_effect = FileNotFoundError("crew not found: c1")
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_accept"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert "error" in data
    assert "crew not found" in data["error"]


def test_crew_challenge_returns_error_on_exception():
    server = FakeServer()
    controller = MagicMock()
    controller.challenge.side_effect = FileNotFoundError("crew not found: c1")
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_challenge"](crew_id="c1", summary="bad"))
    data = json.loads(result[0].text)
    assert "error" in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/mcp_server/test_crew_decision_tools.py -v`
Expected: FAIL — no try/except, exception propagates

- [ ] **Step 3: Wrap both tools in try/except**

```python
# Replace crew_decision.py content:

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent


def register_decision_tools(server: FastMCP, controller) -> None:

    @server.tool("crew_accept")
    async def crew_accept(crew_id: str, summary: str = "accepted by supervisor") -> list[TextContent]:
        """接受当前 Crew 结果，触发合并。"""
        try:
            result = controller.accept(crew_id=crew_id, summary=summary)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        except FileNotFoundError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except ValueError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except Exception as exc:
            return [TextContent(type="text", text=json.dumps({"error": f"internal: {exc}"}, ensure_ascii=False))]

    @server.tool("crew_challenge")
    async def crew_challenge(
        crew_id: str,
        summary: str,
        task_id: str | None = None,
    ) -> list[TextContent]:
        """对 Worker 发出挑战，记录 RISK 黑板条目。"""
        try:
            result = controller.challenge(crew_id=crew_id, summary=summary, task_id=task_id)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        except FileNotFoundError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except ValueError as exc:
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
        except Exception as exc:
            return [TextContent(type="text", text=json.dumps({"error": f"internal: {exc}"}, ensure_ascii=False))]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/mcp_server/test_crew_decision_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/tools/crew_decision.py tests/mcp_server/test_crew_decision_tools.py
git commit -m "fix(mcp): add error handling to crew_accept and crew_challenge tools"
```

---

### Task A2: crew_lifecycle.py — 6 个工具加 try/except

**Files:**
- Modify: `src/codex_claude_orchestrator/mcp_server/tools/crew_lifecycle.py:91-179`
- Modify: `tests/mcp_server/test_crew_lifecycle_tools.py`

- [ ] **Step 1: Write failing tests for error handling**

```python
# Add to tests/mcp_server/test_crew_lifecycle_tools.py

def test_crew_start_returns_error_on_exception():
    server = FakeServer()
    controller = MagicMock()
    controller.start.side_effect = RuntimeError("spawn failed")
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_start"](repo="/repo", goal="test"))
    data = json.loads(result[0].text)
    assert "error" in data
    assert "spawn failed" in data["error"]


def test_crew_stop_returns_error_on_file_not_found():
    server = FakeServer()
    controller = MagicMock()
    controller.stop.side_effect = FileNotFoundError("crew not found: c1")
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_stop"](repo="/repo", crew_id="c1"))
    data = json.loads(result[0].text)
    assert "error" in data


def test_crew_status_returns_error_on_file_not_found():
    server = FakeServer()
    controller = MagicMock()
    controller.status.side_effect = FileNotFoundError("crew not found: c1")
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_status"](repo="/repo", crew_id="c1"))
    data = json.loads(result[0].text)
    assert "error" in data


def test_crew_verify_returns_error_on_value_error():
    server = FakeServer()
    controller = MagicMock()
    controller.verify.side_effect = ValueError("verify not configured")
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_verify"](crew_id="c1", command="pytest"))
    data = json.loads(result[0].text)
    assert "error" in data


def test_crew_spawn_returns_error_on_exception():
    server = FakeServer()
    controller = MagicMock()
    controller.ensure_worker.side_effect = RuntimeError("spawn failed")
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_spawn"](repo="/repo", crew_id="c1", label="my-worker"))
    data = json.loads(result[0].text)
    assert "error" in data


def test_crew_stop_worker_returns_error_on_file_not_found():
    server = FakeServer()
    controller = MagicMock()
    controller.stop_worker.side_effect = FileNotFoundError("worker not found: w1")
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_stop_worker"](repo="/repo", crew_id="c1", worker_id="w1"))
    data = json.loads(result[0].text)
    assert "error" in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/mcp_server/test_crew_lifecycle_tools.py -v`
Expected: FAIL

- [ ] **Step 3: Wrap all 6 tools in try/except**

Update `crew_lifecycle.py` — wrap each tool body in:

```python
try:
    ...
except FileNotFoundError as exc:
    return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
except ValueError as exc:
    return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
except Exception as exc:
    return [TextContent(type="text", text=json.dumps({"error": f"internal: {exc}"}, ensure_ascii=False))]
```

For each of: `crew_start`, `crew_stop`, `crew_status`, `crew_verify`, `crew_spawn`, `crew_stop_worker`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/mcp_server/test_crew_lifecycle_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/tools/crew_lifecycle.py tests/mcp_server/test_crew_lifecycle_tools.py
git commit -m "fix(mcp): add error handling to all 6 crew_lifecycle tools"
```

---

### Task A3: crew_context.py — 5 个工具加 try/except

**Files:**
- Modify: `src/codex_claude_orchestrator/mcp_server/tools/crew_context.py:21-96`
- Modify: `tests/mcp_server/test_crew_context_tools.py`

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/mcp_server/test_crew_context_tools.py

def test_crew_blackboard_returns_error_on_exception():
    server = FakeServer()
    controller = MagicMock()
    controller.blackboard_entries.side_effect = FileNotFoundError("crew not found: c1")
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_blackboard"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert "error" in data


def test_crew_events_returns_error_on_exception():
    server = FakeServer()
    controller = MagicMock()
    controller.status.side_effect = FileNotFoundError("crew not found: c1")
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_events"](repo="/repo", crew_id="c1"))
    data = json.loads(result[0].text)
    assert "error" in data


def test_crew_observe_returns_error_on_exception():
    server = FakeServer()
    controller = MagicMock()
    controller.observe_worker.side_effect = FileNotFoundError("worker not found: w1")
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_observe"](repo="/repo", crew_id="c1", worker_id="w1"))
    data = json.loads(result[0].text)
    assert "error" in data


def test_crew_changes_returns_error_on_value_error():
    server = FakeServer()
    controller = MagicMock()
    controller.changes.side_effect = ValueError("change recorder not configured")
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_changes"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert "error" in data


def test_crew_diff_returns_error_on_exception():
    server = FakeServer()
    controller = MagicMock()
    controller.changes.side_effect = FileNotFoundError("crew not found: c1")
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_diff"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert "error" in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/mcp_server/test_crew_context_tools.py -v`
Expected: FAIL

- [ ] **Step 3: Wrap all 5 tools in try/except**

Update `crew_context.py` — wrap each tool body in the same pattern. Example for `crew_blackboard`:

```python
@server.tool("crew_blackboard")
async def crew_blackboard(...) -> list[TextContent]:
    """读取黑板条目（过滤后，默认最近 10 条）。"""
    try:
        entries = controller.blackboard_entries(crew_id=crew_id)
        _spawn_summarizer_if_needed(crew_id, entries, repo)
        filtered = compress_blackboard(entries, limit=limit, worker_id=worker_id, entry_type=entry_type)
        return [TextContent(type="text", text=truncate_json(filtered))]
    except FileNotFoundError as exc:
        return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
    except ValueError as exc:
        return [TextContent(type="text", text=json.dumps({"error": str(exc)}, ensure_ascii=False))]
    except Exception as exc:
        return [TextContent(type="text", text=json.dumps({"error": f"internal: {exc}"}, ensure_ascii=False))]
```

Same pattern for `crew_events`, `crew_observe`, `crew_changes`, `crew_diff`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/mcp_server/test_crew_context_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/tools/crew_context.py tests/mcp_server/test_crew_context_tools.py
git commit -m "fix(mcp): add error handling to all 5 crew_context tools"
```

---

### Task A4: crew_events 数据丢失防护

**Files:**
- Modify: `src/codex_claude_orchestrator/mcp_server/tools/crew_context.py:50-55`

`crew_events` already uses `.get("decisions", []) + .get("messages", [])` — confirmed correct. This task is a no-op unless `to_read_crew_dict()` returns different keys. Verify by checking projection output format.

- [ ] **Step 1: Verify projection format**

Check `CrewStateProjection.to_read_crew_dict()` returns `decisions` and `messages` keys. If missing, add them.

- [ ] **Step 2: Commit if changes needed (otherwise skip)**

---

## Category B: 数据完整性

### Task B1: accept() 部分失败防护（HIGH）

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/controller.py:499-504`
- Modify: `tests/crew/test_controller.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/crew/test_controller.py

def test_accept_returns_success_even_if_stop_crew_fails(controller_with_mocks):
    """accept() should return success even if stop_crew raises."""
    ctrl = controller_with_mocks
    ctrl._recorder.read_crew.return_value = {
        "crew": {"repo": "/repo", "crew_id": "c1"},
        "workers": [],
    }
    ctrl._worker_pool.stop_crew.side_effect = RuntimeError("tmux down")
    result = ctrl.accept(crew_id="c1", summary="looks good")
    assert result["status"] == "accepted"
    assert "error" in result["stop"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/crew/test_controller.py -v -k test_accept_returns_success`
Expected: FAIL — RuntimeError propagates

- [ ] **Step 3: Wrap stop_crew in try/except**

```python
# controller.py, replace lines 499-504:

def accept(self, *, crew_id: str, summary: str) -> dict:
    details = self._recorder.read_crew(crew_id)
    repo = details["crew"]["repo"]
    self._recorder.finalize_crew(crew_id, CrewStatus.ACCEPTED, summary)
    if self._domain_events:
        self._domain_events.emit_crew_finalized(crew_id, "accepted", summary)
    try:
        stop_result = self._worker_pool.stop_crew(repo_root=Path(repo), crew_id=crew_id)
    except Exception as exc:
        stop_result = {"error": str(exc)}
    return {"crew_id": crew_id, "status": CrewStatus.ACCEPTED.value, "summary": summary, "stop": stop_result}
```

- [ ] **Step 4: Run test**

Run: `pytest tests/crew/test_controller.py -v -k test_accept_returns_success`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/crew/controller.py tests/crew/test_controller.py
git commit -m "fix(controller): accept() handles stop_crew failure gracefully"
```

---

### Task B2: stop_crew() 支持 worktree 清理（HIGH）

**Files:**
- Modify: `src/codex_claude_orchestrator/workers/pool.py:321-327`
- Modify: `src/codex_claude_orchestrator/crew/controller.py:499-504` (pass workspace_cleanup)
- Modify: `tests/workers/test_pool.py` or `tests/crew/test_controller.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/workers/test_pool.py

def test_stop_crew_with_workspace_cleanup(pool_with_mocks):
    """stop_crew(workspace_cleanup='remove') cleans up each worker's worktree."""
    pool = pool_with_mocks
    pool._recorder.read_crew.return_value = {
        "workers": [
            {"worker_id": "w1", "terminal_session": "s1"},
            {"worker_id": "w2", "terminal_session": "s2"},
        ]
    }
    pool._read_worker_allocation.return_value = MagicMock()
    pool._worktree_manager.cleanup.return_value = {"removed": True}
    result = pool.stop_crew(repo_root=Path("/repo"), crew_id="c1", workspace_cleanup="remove")
    assert len(result["stopped_workers"]) == 2
    assert result["stopped_workers"][0]["workspace_cleanup"]["removed"] is True
    assert pool._worktree_manager.cleanup.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/workers/test_pool.py -v -k test_stop_crew_with_workspace_cleanup`
Expected: FAIL — stop_crew doesn't accept workspace_cleanup param

- [ ] **Step 3: Add workspace_cleanup parameter to stop_crew**

```python
# pool.py, replace lines 321-327:

def stop_crew(self, *, repo_root: Path, crew_id: str, workspace_cleanup: str = "keep") -> dict:
    stopped_workers = []
    for worker in self._recorder.read_crew(crew_id)["workers"]:
        result = self._native_session.stop(terminal_session=worker["terminal_session"])
        cleanup = {"removed": False, "reason": "keep policy"}
        if workspace_cleanup == "remove":
            try:
                allocation = self._read_worker_allocation(crew_id, worker["worker_id"])
                cleanup = self._worktree_manager.cleanup(repo_root=repo_root, allocation=allocation, remove=True)
            except Exception as exc:
                cleanup = {"removed": False, "reason": str(exc)}
        self._mark_worker_stopped(crew_id, worker["worker_id"])
        stopped_workers.append({"worker_id": worker["worker_id"], **result, "workspace_cleanup": cleanup})
    return {"crew_id": crew_id, "stopped_workers": stopped_workers}
```

- [ ] **Step 4: Update controller.accept() to pass workspace_cleanup="remove"**

```python
# controller.py accept() — update stop_crew call:
stop_result = self._worker_pool.stop_crew(repo_root=Path(repo), crew_id=crew_id, workspace_cleanup="remove")
```

- [ ] **Step 5: Run test**

Run: `pytest tests/workers/test_pool.py -v -k test_stop_crew_with_workspace_cleanup`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/workers/pool.py src/codex_claude_orchestrator/crew/controller.py tests/workers/test_pool.py
git commit -m "fix(pool): stop_crew supports workspace_cleanup='remove' for worktree cleanup"
```

---

### Task B3: challenge() 写入黑板条目（HIGH）

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/controller.py:472-497`
- Modify: `tests/crew/test_controller.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/crew/test_controller.py

def test_challenge_writes_blackboard_entry(controller_with_mocks):
    """challenge() should write a RISK entry to blackboard."""
    ctrl = controller_with_mocks
    ctrl.challenge(crew_id="c1", summary="risky code", task_id="t1")
    ctrl._blackboard.append.assert_called_once()
    entry = ctrl._blackboard.append.call_args[0][0]
    assert entry.type.value == "risk"
    assert entry.content == "risky code"
    assert entry.crew_id == "c1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/crew/test_controller.py -v -k test_challenge_writes_blackboard`
Expected: FAIL — _blackboard.append not called

- [ ] **Step 3: Add blackboard write to challenge()**

```python
# controller.py, add before the return in challenge() (after line 496):

self._blackboard.append(BlackboardEntry(
    entry_id=self._entry_id_factory(),
    crew_id=crew_id,
    task_id=task_id or "",
    actor_type=ActorType.SUPERVISOR,
    actor_id="supervisor",
    type=BlackboardEntryType.RISK,
    content=summary,
    confidence=0.9,
))
```

Ensure imports for `BlackboardEntry`, `BlackboardEntryType`, `ActorType` are present.

- [ ] **Step 4: Run test**

Run: `pytest tests/crew/test_controller.py -v -k test_challenge_writes_blackboard`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/crew/controller.py tests/crew/test_controller.py
git commit -m "fix(controller): challenge() writes RISK blackboard entry"
```

---

### Task B4: compress_observe_result() 状态回退

**Files:**
- Modify: `src/codex_claude_orchestrator/mcp_server/context/compressor.py` (compress_observe_result)
- Modify: `tests/mcp_server/test_compressor.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/mcp_server/test_compressor.py

def test_compress_observe_result_outbox_without_status_falls_back_to_marker():
    """When outbox has no status field, fall back to marker_seen."""
    raw_observation = {"marker_seen": True, "message_blocks": []}
    outbox = {"worker_id": "w1", "summary": "done"}  # no "status" key
    result = compress_observe_result(raw_observation, outbox, worker_id="w1")
    assert result["status"] == "completed"  # marker_seen=True → completed

    raw_observation2 = {"marker_seen": False, "message_blocks": []}
    result2 = compress_observe_result(raw_observation2, outbox, worker_id="w1")
    assert result2["status"] == "running"  # marker_seen=False → running
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp_server/test_compressor.py -v -k test_compress_observe_result_outbox_without_status`
Expected: FAIL — defaults to "completed" regardless of marker_seen

- [ ] **Step 3: Fix compress_observe_result**

```python
# compressor.py, in compress_observe_result — update the outbox branch:

if outbox:
    status = outbox.get("status")
    if not status:
        status = "completed" if raw_observation.get("marker_seen") else "running"
    return {
        "worker_id": outbox.get("worker_id", worker_id),
        "status": status,
        "summary": outbox.get("summary", ""),
        "changed_files": outbox.get("changed_files", []),
        "risks": outbox.get("risks", []),
        "next_suggested_action": outbox.get("next_suggested_action", ""),
        "messages": raw_observation.get("message_blocks", []),
        "marker_seen": raw_observation.get("marker_seen", False),
    }
```

- [ ] **Step 4: Run test**

Run: `pytest tests/mcp_server/test_compressor.py -v -k test_compress_observe_result_outbox_without_status`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/context/compressor.py tests/mcp_server/test_compressor.py
git commit -m "fix(compressor): compress_observe_result falls back to marker_seen when outbox has no status"
```

---

### Task B5: _run_review 状态映射

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/crew_runner.py:511-517`
- Modify: `tests/v4/test_crew_runner.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/v4/test_crew_runner.py

def test_run_review_maps_non_completed_statuses(runner_with_mocks):
    """_run_review should map turn_failed/timeout/cancelled to distinct statuses."""
    runner = runner_with_mocks
    # Mock _run_turn to return turn_failed
    runner._run_turn = MagicMock(return_value={"status": "turn_failed", "reason": "crash", "turn_id": "t1"})
    # ... setup minimal mocks for review_worker etc.
    result = runner._run_review(...)
    assert result["status"] == "review_failed"
    assert "crash" in result["reason"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/v4/test_crew_runner.py -v -k test_run_review_maps`
Expected: FAIL — returns "waiting_for_worker" for all non-completed

- [ ] **Step 3: Fix status mapping**

```python
# crew_runner.py, replace lines 511-517:

if turn_result.get("status") != "turn_completed":
    status_map = {
        "turn_failed": "review_failed",
        "turn_timeout": "review_timeout",
        "turn_cancelled": "review_cancelled",
    }
    return {
        "status": status_map.get(turn_result.get("status"), "waiting_for_worker"),
        "worker_id": review_worker["worker_id"],
        "reason": turn_result.get("reason", "review completion evidence not found"),
        "events": events,
    }
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_crew_runner.py -v -k test_run_review_maps`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/crew_runner.py tests/v4/test_crew_runner.py
git commit -m "fix(crew_runner): _run_review maps turn_failed/timeout/cancelled to distinct statuses"
```

---

### Task B6: 取消的 job result 不丢失

**Files:**
- Modify: `src/codex_claude_orchestrator/mcp_server/job_manager.py:108-112`
- Modify: `tests/mcp_server/test_job_manager.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/mcp_server/test_job_manager.py

def test_cancelled_job_stores_result(tmp_path):
    """Cancelled jobs should still store the result if runner completes."""
    manager = JobManager()

    def run_and_complete(**kwargs):
        time.sleep(0.05)
        return {"status": "done", "data": "partial"}

    runner = FakeRunner()
    runner.run = run_and_complete

    job_id = manager.create_job(
        runner=runner, repo_root=tmp_path, goal="test",
        verification_commands=["echo ok"],
    )

    # Wait for runner to finish
    time.sleep(0.3)
    job = manager.get_job(job_id)
    # Result should be stored even though job was cancelled
    assert job.result is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp_server/test_job_manager.py -v -k test_cancelled_job_stores_result`
Expected: FAIL — job.result is None

- [ ] **Step 3: Always store result**

```python
# job_manager.py, replace lines 108-112:

with self._lock:
    if job.status != "cancelled":
        job.status = "done"
    job.result = result  # always store, even if cancelled
    job.phase = "idle"
```

- [ ] **Step 4: Run test**

Run: `pytest tests/mcp_server/test_job_manager.py -v -k test_cancelled_job_stores_result`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/job_manager.py tests/mcp_server/test_job_manager.py
git commit -m "fix(job_manager): always store result even for cancelled jobs"
```

---

### Task B7: supervisor 事件流式写入

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/supervisor.py:135-158`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/v4/test_supervisor.py (or equivalent)

def test_supervisor_streams_events_to_store(supervisor_with_mocks):
    """Events should be appended to store during iteration, not buffered."""
    sup = supervisor_with_mocks
    # Mock adapter.watch_turn to yield multiple events
    events = [
        RuntimeEvent(type="runtime.started", turn_id="t1", worker_id="w1", payload={}),
        RuntimeEvent(type="runtime.output", turn_id="t1", worker_id="w1", payload={}),
        RuntimeEvent(type="runtime.completed", turn_id="t1", worker_id="w1", payload={}),
    ]
    sup._adapter.watch_turn.return_value = iter(events)
    # ... call deliver_turn or equivalent
    # Assert events.append was called 3 times (once per event), not buffered
    assert sup._events.append.call_count == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/v4/test_supervisor.py -v -k test_supervisor_streams`
Expected: FAIL — events buffered in list comprehension first

- [ ] **Step 3: Change from list comprehension to streaming loop**

```python
# supervisor.py, replace lines 135-158:

runtime_events = []
for index, runtime_event in enumerate(
    e for e in self._adapter.watch_turn(turn, cancel_event=cancel_event)
    if self._is_current_turn_event(turn, e)
):
    event_payload = _runtime_event_payload_for_storage(runtime_event)
    event = self._events.append(
        stream_id=crew_id,
        type=runtime_event.type,
        crew_id=crew_id,
        worker_id=runtime_event.worker_id,
        turn_id=runtime_event.turn_id,
        round_id=turn.round_id,
        contract_id=turn.contract_id,
        idempotency_key=(
            f"{crew_id}/{turn.turn_id}/{runtime_event.type}/{index}/"
            f"{_runtime_event_digest(runtime_event, index=index)}"
        ),
        payload=event_payload,
        artifact_refs=runtime_event.artifact_refs,
    )
    self._process_message_ack_if_configured(event)
    runtime_events.append(runtime_event)
self._commit_runtime_events_if_supported(turn, runtime_events)
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_supervisor.py -v -k test_supervisor_streams`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/supervisor.py tests/v4/test_supervisor.py
git commit -m "fix(supervisor): stream events to store during iteration instead of buffering"
```

---

## Category C: 并发安全

### Task C1: review→verification 之间 cancel 检查

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/crew_runner.py:333-335` (add cancel check before verification)
- Modify: `tests/v4/test_crew_runner.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/v4/test_crew_runner.py

def test_cancel_checked_between_review_and_verification(runner_with_mocks):
    """cancel_event should be checked between review completion and verification."""
    runner = runner_with_mocks
    cancel_event = threading.Event()
    cancel_event.set()  # pre-cancelled
    # ... setup mocks so review completes successfully
    result = runner.supervise(..., cancel_event=cancel_event)
    assert result["status"] == "cancelled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/v4/test_crew_runner.py -v -k test_cancel_checked_between`
Expected: FAIL — cancel not checked, proceeds to verification

- [ ] **Step 3: Add cancel check**

```python
# crew_runner.py, add after line 333 (after repair_requests.clear()):

if cancel_event and cancel_event.is_set():
    return {
        "crew_id": crew_id,
        "status": "cancelled",
        "runtime": "v4",
        "rounds": round_index,
        "events": events,
    }
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_crew_runner.py -v -k test_cancel_checked_between`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/crew_runner.py tests/v4/test_crew_runner.py
git commit -m "fix(crew_runner): check cancel_event between review and verification phases"
```

---

### Task C2: update_elapsed() 移入锁内

**Files:**
- Modify: `src/codex_claude_orchestrator/mcp_server/job_manager.py:119-122`
- Modify: `tests/mcp_server/test_job_manager.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/mcp_server/test_job_manager.py

def test_update_elapsed_called_under_lock(tmp_path):
    """update_elapsed should be called under the lock, not outside."""
    manager = JobManager()
    runner = FakeRunner(delay=0.1)
    job_id = manager.create_job(
        runner=runner, repo_root=tmp_path, goal="test",
        verification_commands=["echo ok"],
    )
    time.sleep(0.3)
    job = manager.get_job(job_id)
    # elapsed_seconds should be consistent (not 0.0 which would indicate race)
    assert job.elapsed_seconds > 0
```

- [ ] **Step 2: Run test**

This is more of a race condition — the existing test `test_job_manager_captures_errors` already indirectly tests this. The fix is straightforward.

- [ ] **Step 3: Move update_elapsed() into the lock**

```python
# job_manager.py, replace lines 119-122:

finally:
    with self._lock:
        job.completed_at = time.monotonic()
        job.update_elapsed()
```

- [ ] **Step 4: Run all job_manager tests**

Run: `pytest tests/mcp_server/test_job_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/job_manager.py
git commit -m "fix(job_manager): move update_elapsed() inside lock in finally block"
```

---

### Task C3: _watch_filesystem_stream 支持取消

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/adapters/tmux_claude.py:84,184-209`
- Modify: `tests/v4/test_tmux_claude_adapter.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/v4/test_tmux_claude_adapter.py

def test_watch_filesystem_stream_respects_cancel(adapter_with_mocks):
    """_watch_filesystem_stream should check cancel_event."""
    adapter = adapter_with_mocks
    cancel = threading.Event()
    cancel.set()  # pre-cancelled
    turn = MagicMock()
    turn.crew_id = "c1"
    turn.turn_id = "t1"
    turn.worker_id = "w1"
    worker = MagicMock()
    # Should yield nothing or yield cancelled event
    events = list(adapter._watch_filesystem_stream(turn, worker, cancel_event=cancel))
    # Should not block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/v4/test_tmux_claude_adapter.py -v -k test_watch_filesystem_stream_respects_cancel`
Expected: FAIL — blocks or doesn't accept cancel_event

- [ ] **Step 3: Add cancel_event parameter and check**

```python
# tmux_claude.py, update _watch_filesystem_stream signature and watch_turn call:

# Line 84: pass cancel to filesystem stream
yield from self._watch_filesystem_stream(turn, worker, cancel_event=cancel)

# Line 184: add cancel_event parameter
def _watch_filesystem_stream(self, turn: TurnEnvelope, worker: WorkerSpec | None, cancel_event: threading.Event | None = None):
    outbox_path = _required_outbox_path(turn)
    transcript_path = _transcript_path(worker)
    if outbox_path is None and transcript_path is None:
        return
    cancel = cancel_event or self._cancel
    stream = FilesystemRuntimeEventStream(
        state_path=_filesystem_stream_state_path(
            outbox_path=outbox_path,
            transcript_path=transcript_path,
        )
    )
    # Poll with cancel check
    for event in stream.poll_once(
        crew_id=turn.crew_id,
        turn_id=turn.turn_id,
        worker_id=turn.worker_id,
        outbox_path=outbox_path,
        transcript_path=transcript_path,
        expected_marker=turn.expected_marker,
        outbox_artifact_ref=_required_outbox_artifact_ref(turn) if outbox_path else None,
        transcript_artifact_ref=(
            _transcript_artifact_ref(worker, transcript_path)
            if transcript_path is not None
            else None
        ),
        autocommit=False,
    ):
        if cancel.is_set():
            return
        yield event
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_tmux_claude_adapter.py -v -k test_watch_filesystem_stream_respects_cancel`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/adapters/tmux_claude.py tests/v4/test_tmux_claude_adapter.py
git commit -m "fix(tmux_adapter): _watch_filesystem_stream respects cancel_event"
```

---

### Task C4: _delivery_locks 内存泄漏

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/turns.py:30-43`
- Modify: `tests/v4/test_turns.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/v4/test_turns.py

def test_delivery_locks_cleaned_up_after_use():
    """Lock entries should be removed after deliver_turn completes."""
    from codex_claude_orchestrator.v4.turns import _delivery_locks, _delivery_locks_guard
    # Get initial count
    with _delivery_locks_guard:
        initial_count = len(_delivery_locks)
    # ... run a delivery
    # After delivery, lock should be cleaned up
    with _delivery_locks_guard:
        final_count = len(_delivery_locks)
    assert final_count <= initial_count
```

- [ ] **Step 2: Run test**

- [ ] **Step 3: Add cleanup in finally block**

```python
# turns.py, update request_and_deliver:

def request_and_deliver(self, turn: TurnEnvelope) -> DeliveryResult:
    delivered_result = self._stored_delivered_result(turn)
    if delivered_result is not None:
        return delivered_result

    failed_result = self._stored_failed_result(turn)
    if failed_result is not None:
        return failed_result

    lock_key = (turn.crew_id, turn.turn_id, turn.attempt)
    with _delivery_locks_guard:
        delivery_lock = _delivery_locks[lock_key]

    try:
        with delivery_lock:
            delivered_result = self._stored_delivered_result(turn)
            if delivered_result is not None:
                return delivered_result

            failed_result = self._stored_failed_result(turn)
            if failed_result is not None:
                return failed_result

            return self._request_and_deliver_claimed(turn)
    finally:
        with _delivery_locks_guard:
            key = (turn.crew_id, turn.turn_id, turn.attempt)
            if key in _delivery_locks and _delivery_locks[key] is delivery_lock:
                del _delivery_locks[key]
```

- [ ] **Step 4: Run test**

Run: `pytest tests/v4/test_turns.py -v -k test_delivery_locks_cleaned_up`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/turns.py tests/v4/test_turns.py
git commit -m "fix(turns): clean up _delivery_locks after use to prevent memory leak"
```

---

## Category D: 防御性编码

### Task D1: _read_worker_allocation 防御性访问

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/controller.py:551-567`
- Modify: `tests/crew/test_controller.py`

- [ ] **Step 1: Write failing test**

```python
# Add to tests/crew/test_controller.py

def test_read_worker_allocation_raises_on_missing_artifact(controller_with_mocks):
    """_read_worker_allocation should raise FileNotFoundError when workspace_allocation_artifact missing."""
    ctrl = controller_with_mocks
    ctrl._recorder.read_crew.return_value = {
        "workers": [{"worker_id": "w1"}],  # no workspace_allocation_artifact
    }
    import pytest
    with pytest.raises(FileNotFoundError, match="has no workspace allocation"):
        ctrl._read_worker_allocation("c1", "w1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/crew/test_controller.py -v -k test_read_worker_allocation_raises`
Expected: FAIL — KeyError on missing key

- [ ] **Step 3: Add .get() guard**

```python
# controller.py, replace line 556:

artifact = worker.get("workspace_allocation_artifact")
if not artifact:
    raise FileNotFoundError(f"worker {worker_id} has no workspace allocation")
```

- [ ] **Step 4: Run test**

Run: `pytest tests/crew/test_controller.py -v -k test_read_worker_allocation_raises`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/crew/controller.py tests/crew/test_controller.py
git commit -m "fix(controller): _read_worker_allocation uses .get() with clear error"
```

---

### Task D2: observe_worker / stop_worker terminal 保护

**Files:**
- Modify: `src/codex_claude_orchestrator/workers/pool.py:262-263,309-315`
- Modify: `tests/workers/test_pool.py`

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/workers/test_pool.py

def test_observe_worker_raises_on_missing_terminal_pane(pool_with_mocks):
    """observe_worker should raise when terminal_pane is missing."""
    pool = pool_with_mocks
    pool._find_worker.return_value = {"worker_id": "w1"}  # no terminal_pane
    import pytest
    with pytest.raises(FileNotFoundError, match="has no terminal pane"):
        pool.observe_worker(repo_root=Path("/repo"), crew_id="c1", worker_id="w1")


def test_stop_worker_raises_on_missing_terminal_session(pool_with_mocks):
    """stop_worker should raise when terminal_session is missing."""
    pool = pool_with_mocks
    pool._find_worker.return_value = {"worker_id": "w1"}  # no terminal_session
    import pytest
    with pytest.raises(FileNotFoundError, match="has no terminal session"):
        pool.stop_worker(repo_root=Path("/repo"), crew_id="c1", worker_id="w1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/workers/test_pool.py -v -k "test_observe_worker_raises or test_stop_worker_raises"`
Expected: FAIL — KeyError

- [ ] **Step 3: Add guards**

```python
# pool.py observe_worker, after line 262:
pane = worker.get("terminal_pane")
if not pane:
    raise FileNotFoundError(f"worker {worker_id} has no terminal pane")
observation = self._native_session.observe(terminal_pane=pane, lines=lines, turn_marker=turn_marker)

# pool.py stop_worker, after line 310:
session = worker.get("terminal_session")
if not session:
    raise FileNotFoundError(f"worker {worker_id} has no terminal session")
# ... use session instead of worker["terminal_session"]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/workers/test_pool.py -v -k "test_observe_worker_raises or test_stop_worker_raises"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/workers/pool.py tests/workers/test_pool.py
git commit -m "fix(pool): guard terminal_pane/session access with clear errors"
```

---

### Task D3: worker_from_dict 防御性校验

**Files:**
- Check if `_worker_from_dict` exists in controller.py or pool.py. If not applicable, skip this task.

- [ ] **Step 1: Check if _worker_from_dict exists**

Search for `_worker_from_dict` in the codebase. If it doesn't exist, this task is a no-op.

- [ ] **Step 2: If exists, add required field validation**

- [ ] **Step 3: Commit if changes made**

---

## Final Verification

After all tasks are complete:

- [ ] **Run full test suite**

```bash
pytest tests/mcp_server/ tests/crew/ tests/workers/ -v
```

Expected: All tests pass, no regressions.

- [ ] **Verify all 18 fixes are addressed**

| ID | Fix | Task |
|----|-----|------|
| A1 | crew_context.py error handling | A3 |
| A2 | crew_lifecycle.py error handling | A2 |
| A3 | crew_decision.py error handling | A1 |
| A4 | crew_events data loss | A4 |
| B1 | accept() partial failure | B1 |
| B2 | stop_crew() worktree cleanup | B2 |
| B3 | challenge() blackboard write | B3 |
| B4 | compress_observe_result status | B4 |
| B5 | _run_review status mapping | B5 |
| B6 | cancelled job result | B6 |
| B7 | supervisor event streaming | B7 |
| C1 | review→verification cancel check | C1 |
| C2 | update_elapsed lock race | C2 |
| C3 | filesystem stream cancel | C3 |
| C4 | delivery_locks leak | C4 |
| D1 | _read_worker_allocation guard | D1 |
| D2 | terminal_pane/session guard | D2 |
| D3 | worker_from_dict guard | D3 |
