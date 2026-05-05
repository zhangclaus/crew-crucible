# MCP Server-Driven Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign MCP mode so the Server drives the full supervision loop internally with blocking polling, and uses MCP `sampling/createMessage` to ask the supervisor (LLM) for strategic decisions. Supervisor is decoupled and replaceable.

**Architecture:** `crew_run` becomes a single long-running tool call. Internally it runs a blocking loop: poll workers → auto-verify → if strategic decision needed, call `ctx.session.create_message()` to ask the supervisor → parse response → execute decision → repeat. The rule engine fallback is removed.

**Tech Stack:** Python, MCP SDK (`mcp.server.fastmcp.Context`, `mcp.types`), pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `src/codex_claude_orchestrator/crew/supervisor_loop.py` | New `run()` with sampling_fn, `_wait_for_workers()`, `_ask_supervisor()`, `_parse_decision()`, `_execute_decision()`. Remove `run_step()`, `_poll_workers()`, `_build_snapshot()` |
| Modify | `src/codex_claude_orchestrator/mcp_server/tools/crew_execution.py` | Rewrite `crew_run` to be long-running with `ctx: Context` and sampling. Remove `crew_verify`, `crew_merge_plan` |
| Modify | `src/codex_claude_orchestrator/mcp_server/tools/crew_decision.py` | Remove `crew_decide` and `crew_spawn`. Keep `crew_accept` and `crew_challenge` |
| Delete | `src/codex_claude_orchestrator/crew/loop_step_result.py` | No longer needed |
| Modify | `tests/crew/test_supervisor_loop_step.py` | Rewrite tests for new `run()` method with sampling_fn |
| Delete | `tests/crew/test_loop_step_result.py` | No longer needed |

---

### Task 1: Rewrite `supervisor_loop.py` — new `run()` with sampling

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/supervisor_loop.py:39-738`
- Test: `tests/crew/test_supervisor_loop_step.py`

- [ ] **Step 1: Write failing tests for new `run()` method**

Replace `tests/crew/test_supervisor_loop_step.py` with tests for the new `run()` method:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from codex_claude_orchestrator.crew.supervisor_loop import CrewSupervisorLoop


def test_run_accepts_when_verify_passes_and_supervisor_says_accept():
    """run() should return accept result when verification passes and supervisor says accept."""
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [],
        "decisions": [],
        "messages": [],
    }
    controller.accept.return_value = {"status": "accepted", "crew_id": "c1"}

    loop = CrewSupervisorLoop(controller=controller)

    # Mock sampling_fn returns "accept"
    async def mock_sampling(messages, system_prompt, max_tokens):
        result = MagicMock()
        result.content.text = "accept"
        return result

    with patch.object(loop, "_wait_for_workers"), \
         patch.object(loop, "_auto_verify", return_value={"passed": True, "failure_count": 0}):
        result = loop.run(
            crew_id="c1",
            max_rounds=3,
            verification_commands=["pytest"],
            sampling_fn=mock_sampling,
        )

    assert result["status"] == "accepted"
    controller.accept.assert_called_once_with(crew_id="c1")


def test_run_auto_challenges_when_verify_fails_less_than_3():
    """run() should auto-challenge when verification fails < 3 times."""
    controller = MagicMock()
    # First call: workers running. After challenge, workers idle again, verify passes.
    call_count = {"n": 0}
    def status_side_effect(**kw):
        call_count["n"] += 1
        if call_count["n"] <= 1:
            return {
                "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
                "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
                "blackboard": [], "decisions": [], "messages": [],
            }
        return {
            "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
            "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
            "blackboard": [], "decisions": [], "messages": [],
        }
    controller.status.side_effect = status_side_effect
    controller.accept.return_value = {"status": "accepted", "crew_id": "c1"}

    loop = CrewSupervisorLoop(controller=controller)

    async def mock_sampling(messages, system_prompt, max_tokens):
        result = MagicMock()
        result.content.text = "accept"
        return result

    # First verify fails (count=1), second passes
    verify_results = [
        {"passed": False, "failure_count": 1, "summary": "fail"},
        {"passed": True, "failure_count": 0},
    ]
    verify_call = {"n": 0}
    def auto_verify_side_effect(*args, **kwargs):
        idx = verify_call["n"]
        verify_call["n"] += 1
        return verify_results[min(idx, len(verify_results) - 1)]

    with patch.object(loop, "_wait_for_workers"), \
         patch.object(loop, "_auto_verify", side_effect=auto_verify_side_effect), \
         patch.object(loop, "_auto_challenge"):
        result = loop.run(
            crew_id="c1",
            max_rounds=3,
            verification_commands=["pytest"],
            sampling_fn=mock_sampling,
        )

    assert result["status"] == "accepted"


def test_run_asks_supervisor_when_verify_fails_3_times():
    """run() should call sampling_fn when verification fails >= 3 times."""
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [], "decisions": [], "messages": [],
    }

    loop = CrewSupervisorLoop(controller=controller)

    sampling_calls = []
    async def mock_sampling(messages, system_prompt, max_tokens):
        sampling_calls.append({"messages": messages, "system_prompt": system_prompt})
        result = MagicMock()
        result.content.text = "accept"
        return result

    with patch.object(loop, "_wait_for_workers"), \
         patch.object(loop, "_auto_verify", return_value={"passed": False, "failure_count": 3, "summary": "fail 3x"}):
        loop.run(
            crew_id="c1",
            max_rounds=3,
            verification_commands=["pytest"],
            sampling_fn=mock_sampling,
        )

    assert len(sampling_calls) == 1
    assert "验证失败 3 次" in sampling_calls[0]["messages"][0].content.text


def test_run_spawns_worker_when_supervisor_says_spawn():
    """run() should call controller.ensure_worker when supervisor says spawn_worker."""
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [], "decisions": [], "messages": [],
    }

    loop = CrewSupervisorLoop(controller=controller)

    async def mock_sampling(messages, system_prompt, max_tokens):
        result = MagicMock()
        result.content.text = 'spawn_worker(label="fixer", mission="fix the tests")'
        return result

    with patch.object(loop, "_wait_for_workers"), \
         patch.object(loop, "_auto_verify", return_value={"passed": False, "failure_count": 3, "summary": "fail"}):
        result = loop.run(
            crew_id="c1",
            max_rounds=1,
            verification_commands=["pytest"],
            sampling_fn=mock_sampling,
        )

    controller.ensure_worker.assert_called_once()
    call_kwargs = controller.ensure_worker.call_args
    assert call_kwargs[1]["crew_id"] == "c1"


def test_run_returns_max_rounds_when_exhausted():
    """run() should return max_rounds_reached when loop exhausts."""
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [{"worker_id": "w1", "status": "idle", "role": "implementer"}],
        "blackboard": [], "decisions": [], "messages": [],
    }

    loop = CrewSupervisorLoop(controller=controller)

    async def mock_sampling(messages, system_prompt, max_tokens):
        result = MagicMock()
        result.content.text = "observe"
        return result

    with patch.object(loop, "_wait_for_workers"), \
         patch.object(loop, "_auto_verify", return_value={"passed": False, "failure_count": 0, "summary": "no verify"}):
        result = loop.run(
            crew_id="c1",
            max_rounds=2,
            verification_commands=[],
            sampling_fn=mock_sampling,
        )

    assert result["status"] == "max_rounds_reached"
    assert result["rounds"] == 2


def test_parse_decision_accept():
    loop = CrewSupervisorLoop(controller=MagicMock())
    assert loop._parse_decision("accept") == {"action": "accept"}


def test_parse_decision_spawn_worker():
    loop = CrewSupervisorLoop(controller=MagicMock())
    result = loop._parse_decision('spawn_worker(label="fixer", mission="fix tests")')
    assert result["action"] == "spawn_worker"
    assert result["label"] == "fixer"
    assert result["mission"] == "fix tests"


def test_parse_decision_challenge():
    loop = CrewSupervisorLoop(controller=MagicMock())
    result = loop._parse_decision('challenge(worker_id="w1", goal="improve coverage")')
    assert result["action"] == "challenge"
    assert result["worker_id"] == "w1"
    assert result["goal"] == "improve coverage"


def test_parse_decision_unknown_defaults_to_observe():
    loop = CrewSupervisorLoop(controller=MagicMock())
    assert loop._parse_decision("I'm not sure what to do") == {"action": "observe"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/crew/test_supervisor_loop_step.py -v`
Expected: FAIL — `run()` doesn't accept `crew_id`/`sampling_fn` parameters, `_parse_decision` doesn't exist

- [ ] **Step 3: Rewrite `supervisor_loop.py` — add new methods**

Add these methods to `CrewSupervisorLoop` (after the existing `__init__`, before `run`):

```python
def run(
    self,
    crew_id: str,
    max_rounds: int,
    verification_commands: list[str],
    sampling_fn,  # async (messages, system_prompt, max_tokens) -> CreateMessageResult
) -> dict:
    """完整监督循环。阻塞运行，需要决策时调 sampling_fn。"""
    for round_index in range(1, max_rounds + 1):
        # 1. 阻塞等待 Worker 完成
        self._wait_for_workers(crew_id)

        # 2. 自动验证
        verify_result = self._auto_verify(crew_id, verification_commands)

        if verify_result.get("passed"):
            # 3a. 验证通过 → 询问 supervisor 是否 accept
            decision = self._ask_supervisor(
                sampling_fn, crew_id, "verification_passed", verify_result
            )
            if decision.get("action") == "accept":
                return self._do_accept(crew_id)

        failure_count = verify_result.get("failure_count", 0)
        if failure_count >= 3:
            # 3b. 失败 >= 3 次 → 询问 supervisor
            decision = self._ask_supervisor(
                sampling_fn, crew_id, "verification_failed", verify_result
            )
            self._execute_decision(crew_id, decision)
            continue

        # 3c. 失败 < 3 次 → 自动挑战
        self._auto_challenge(crew_id, verify_result)

    return {"crew_id": crew_id, "status": "max_rounds_reached", "rounds": max_rounds}

def _wait_for_workers(self, crew_id: str) -> None:
    """阻塞轮询，直到所有 Worker 完成。"""
    while True:
        details = self._controller.status(crew_id=crew_id)
        workers = details.get("workers", [])
        all_done = all(
            w.get("status") in ("idle", "stopped", "failed")
            for w in workers
        )
        if all_done:
            return
        time.sleep(self._poll_interval_seconds)

def _ask_supervisor(
    self, sampling_fn, crew_id: str, situation: str, context: dict
) -> dict:
    """通过 sampling 请求 supervisor 做战略决策。"""
    import asyncio
    import json
    import mcp.types as types

    from codex_claude_orchestrator.mcp_server.context.compressor import compress_crew_status

    compressed = compress_crew_status(
        self._controller.status(crew_id=crew_id)
    )
    prompt = self._build_decision_prompt(situation, context, compressed)

    result = asyncio.get_event_loop().run_until_complete(
        sampling_fn(
            messages=[
                types.SamplingMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt),
                )
            ],
            system_prompt="你是 Crew supervisor，负责战略决策。根据提供的 context 选择下一步行动。回复格式：accept / spawn_worker(label, mission) / challenge(worker_id, goal)",
            max_tokens=500,
        )
    )
    return self._parse_decision(result.content.text)

def _build_decision_prompt(self, situation: str, context: dict, status: dict) -> str:
    """构建决策提示。"""
    import json

    if situation == "verification_passed":
        return (
            f"## 验证通过\n\n"
            f"当前状态：{json.dumps(status, ensure_ascii=False)}\n\n"
            f"验证结果：{json.dumps(context, ensure_ascii=False)}\n\n"
            f"请确认是否 accept。"
        )
    if situation == "verification_failed":
        return (
            f"## 验证失败 {context.get('failure_count', '?')} 次\n\n"
            f"当前状态：{json.dumps(status, ensure_ascii=False)}\n\n"
            f"验证结果：{json.dumps(context, ensure_ascii=False)}\n\n"
            f"请选择下一步：\n"
            f"1. spawn_worker(label, mission) — spawn 新 Worker\n"
            f"2. accept — 跳过验证接受结果\n"
            f"3. challenge(worker_id, goal) — 对现有 Worker 发出新挑战"
        )
    return f"## {situation}\n\n{json.dumps(context, ensure_ascii=False)}"

def _parse_decision(self, response: str) -> dict:
    """解析 supervisor 的决策回复。"""
    import re

    text = response.strip()

    if text.startswith("accept"):
        return {"action": "accept"}

    match = re.match(r'spawn_worker\((.+)\)', text)
    if match:
        params = dict(re.findall(r"(\w+)=['\"]([^'\"]+)['\"]", match.group(1)))
        return {"action": "spawn_worker", **params}

    match = re.match(r'challenge\((.+)\)', text)
    if match:
        params = dict(re.findall(r"(\w+)=['\"]([^'\"]+)['\"]", match.group(1)))
        return {"action": "challenge", **params}

    return {"action": "observe"}

def _execute_decision(self, crew_id: str, decision: dict) -> None:
    """执行 supervisor 的决策。"""
    from codex_claude_orchestrator.crew.models import (
        AuthorityLevel, WorkerContract, WorkspacePolicy,
    )

    if decision["action"] == "spawn_worker":
        contract = WorkerContract(
            contract_id=f"contract-{decision.get('label', 'worker')}",
            label=decision.get("label", "worker"),
            mission=decision.get("mission", ""),
            required_capabilities=["inspect_code", "edit_source"],
            authority_level=AuthorityLevel.source_write,
            workspace_policy=WorkspacePolicy.worktree,
        )
        self._controller.ensure_worker(crew_id=crew_id, contract=contract)
    elif decision["action"] == "accept":
        self._controller.accept(crew_id=crew_id)
    elif decision["action"] == "challenge":
        self._controller.challenge(
            crew_id=crew_id,
            worker_id=decision.get("worker_id", ""),
            goal=decision.get("goal", ""),
        )

def _do_accept(self, crew_id: str) -> dict:
    """执行 accept 并返回结果。"""
    return self._controller.accept(crew_id=crew_id)
```

- [ ] **Step 4: Remove old methods**

Delete these methods from `CrewSupervisorLoop`:
- `run_step()` (lines 671-710)
- `_poll_workers()` (lines 712-717)
- `_build_snapshot()` (lines 730-738)

Also remove the old `run()` method (lines 39-669) — it's replaced by the new `run()`.

Keep: `__init__`, `_wait_for_marker()`, `_auto_verify()`, `_auto_challenge()`, and all helper methods.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/crew/test_supervisor_loop_step.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/crew/supervisor_loop.py tests/crew/test_supervisor_loop_step.py
git commit -m "feat: rewrite supervisor_loop with sampling-based run() method"
```

---

### Task 2: Rewrite `crew_execution.py` — long-running crew_run with sampling

**Files:**
- Modify: `src/codex_claude_orchestrator/mcp_server/tools/crew_execution.py`
- Test: `tests/crew/test_supervisor_loop_step.py` (existing tests cover the loop; integration test below)

- [ ] **Step 1: Write test for new crew_run**

Add to `tests/crew/test_supervisor_loop_step.py`:

```python
def test_crew_run_passes_sampling_fn_to_loop():
    """crew_run should pass ctx.session.create_message as sampling_fn to supervision_loop.run()."""
    from unittest.mock import AsyncMock, MagicMock

    from codex_claude_orchestrator.mcp_server.tools.crew_execution import register_execution_tools

    mock_server = MagicMock()
    registered_tools = {}
    def mock_tool(name):
        def decorator(fn):
            registered_tools[name] = fn
            return fn
        return decorator
    mock_server.tool = mock_tool

    mock_controller = MagicMock()
    mock_loop = MagicMock()
    mock_loop.run.return_value = {"crew_id": "c1", "status": "accepted"}

    register_execution_tools(mock_server, mock_controller, supervision_loop=mock_loop)

    # Verify crew_run is registered
    assert "crew_run" in registered_tools
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/crew/test_supervisor_loop_step.py::test_crew_run_passes_sampling_fn_to_loop -v`
Expected: FAIL (import or assertion error)

- [ ] **Step 3: Rewrite `crew_execution.py`**

Replace entire file:

```python
from __future__ import annotations

import json

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import TextContent


def register_execution_tools(server: FastMCP, controller, supervision_loop=None) -> None:

    @server.tool("crew_run")
    async def crew_run(
        crew_id: str,
        ctx: Context,
        max_rounds: int = 3,
        verification_commands: list[str] | None = None,
    ) -> list[TextContent]:
        """运行完整监督循环。需要决策时通过 sampling 请求 supervisor。长时间运行，调一次等最终结果。"""
        if supervision_loop is None:
            return [TextContent(type="text", text=json.dumps({
                "error": "supervision_loop not initialized"
            }))]

        result = supervision_loop.run(
            crew_id=crew_id,
            max_rounds=max_rounds,
            verification_commands=verification_commands or [],
            sampling_fn=lambda msgs, sys_prompt, max_tok: ctx.session.create_message(
                messages=msgs,
                max_tokens=max_tok,
                system_prompt=sys_prompt,
            ),
        )
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/crew/test_supervisor_loop_step.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/tools/crew_execution.py tests/crew/test_supervisor_loop_step.py
git commit -m "feat: rewrite crew_run as long-running tool with sampling"
```

---

### Task 3: Simplify `crew_decision.py` — remove crew_decide and crew_spawn

**Files:**
- Modify: `src/codex_claude_orchestrator/mcp_server/tools/crew_decision.py`

- [ ] **Step 1: Write test for simplified crew_decision**

Add to `tests/crew/test_supervisor_loop_step.py`:

```python
def test_crew_decision_only_registers_accept_and_challenge():
    """crew_decision should only register crew_accept and crew_challenge tools."""
    from unittest.mock import MagicMock

    from codex_claude_orchestrator.mcp_server.tools.crew_decision import register_decision_tools

    mock_server = MagicMock()
    registered_tools = {}
    def mock_tool(name):
        def decorator(fn):
            registered_tools[name] = fn
            return fn
        return decorator
    mock_server.tool = mock_tool

    register_decision_tools(mock_server, MagicMock())

    assert "crew_accept" in registered_tools
    assert "crew_challenge" in registered_tools
    assert "crew_decide" not in registered_tools
    assert "crew_spawn" not in registered_tools
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/crew/test_supervisor_loop_step.py::test_crew_decision_only_registers_accept_and_challenge -v`
Expected: FAIL (crew_decide and crew_spawn still registered)

- [ ] **Step 3: Simplify `crew_decision.py`**

Replace entire file:

```python
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent


def register_decision_tools(server: FastMCP, controller) -> None:

    @server.tool("crew_accept")
    async def crew_accept(crew_id: str) -> list[TextContent]:
        """接受当前 Crew 结果，触发合并。"""
        result = controller.accept(crew_id=crew_id)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    @server.tool("crew_challenge")
    async def crew_challenge(
        crew_id: str,
        worker_id: str,
        goal: str,
    ) -> list[TextContent]:
        """对 Worker 发出自定义挑战。"""
        result = controller.challenge(crew_id=crew_id, worker_id=worker_id, goal=goal)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/crew/test_supervisor_loop_step.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/tools/crew_decision.py tests/crew/test_supervisor_loop_step.py
git commit -m "refactor: remove crew_decide and crew_spawn tools, keep accept and challenge"
```

---

### Task 4: Delete `loop_step_result.py` and its tests

**Files:**
- Delete: `src/codex_claude_orchestrator/crew/loop_step_result.py`
- Delete: `tests/crew/test_loop_step_result.py`

- [ ] **Step 1: Verify no remaining imports of LoopStepResult**

Run: `grep -r "loop_step_result\|LoopStepResult" src/ tests/ --include="*.py" | grep -v __pycache__ | grep -v ".pyc"`
Expected: Only matches in the files we're about to delete (and possibly supervisor_loop.py which we already cleaned up in Task 1)

- [ ] **Step 2: Delete the files**

```bash
rm src/codex_claude_orchestrator/crew/loop_step_result.py
rm tests/crew/test_loop_step_result.py
```

- [ ] **Step 3: Run tests to verify nothing breaks**

Run: `pytest tests/crew/ -v`
Expected: ALL PASS (no imports of deleted module)

- [ ] **Step 4: Commit**

```bash
git add -u src/codex_claude_orchestrator/crew/loop_step_result.py tests/crew/test_loop_step_result.py
git commit -m "chore: delete LoopStepResult and its tests (no longer needed)"
```

---

### Task 5: Update `server.py` imports if needed and full test run

**Files:**
- Modify (if needed): `src/codex_claude_orchestrator/mcp_server/server.py`

- [ ] **Step 1: Check server.py imports**

Read `src/codex_claude_orchestrator/mcp_server/server.py` and verify it imports from the correct modules. The `register_execution_tools` signature changed (now expects `FastMCP` instead of `Server`), but `server.py` passes `server` which is already a `FastMCP` instance, so no change should be needed.

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 3: Fix any broken imports or tests**

If any tests fail due to removed `LoopStepResult` or changed signatures, fix them.

- [ ] **Step 4: Commit fixes (if any)**

```bash
git add -A
git commit -m "fix: update imports and tests after supervisor redesign"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full test suite one final time**

Run: `pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Verify no stale references**

Run: `grep -r "run_step\|LoopStepResult\|crew_decide\|crew_spawn\|auto_decide\|_poll_workers\|_build_snapshot" src/ tests/ --include="*.py" | grep -v __pycache__`
Expected: No matches (all removed)

- [ ] **Step 3: Final commit (if needed)**

```bash
git add -A
git commit -m "chore: clean up stale references after MCP server-driven redesign"
```
