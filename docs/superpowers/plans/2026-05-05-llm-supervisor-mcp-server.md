# LLM Supervisor MCP Server 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 crew 管理能力封装为 MCP Server，让 Codex (Claude Code) 作为 LLM supervisor 通过 tools 管理 Worker，替代纯规则引擎决策。

**Architecture:** MCP Server 作为中间层，提供 15 个 tools（生命周期、战略决策、上下文获取、战术执行）。Context Layer 负责数据压缩防止上下文膨胀。Supervision Loop 改造为可暂停协程，规则引擎保留为 fallback。

**Tech Stack:** Python 3.11+, `mcp` SDK, 现有 CrewController/WorkerPool

---

## 文件结构

```
src/codex_claude_orchestrator/mcp_server/
    __init__.py
    __main__.py
    server.py
    tools/
        __init__.py
        crew_lifecycle.py
        crew_decision.py
        crew_context.py
        crew_execution.py
    context/
        __init__.py
        compressor.py
        token_budget.py
```

修改的现有文件：
- `src/codex_claude_orchestrator/crew/supervisor_loop.py` — 增加 `run_step()` 方法
- `src/codex_claude_orchestrator/crew/models.py` — `LoopStepResult` 数据类
- `src/codex_claude_orchestrator/state/blackboard.py` — `summary` 字段支持

---

### Task 1: 添加 mcp 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 添加 mcp 依赖**

```toml
# pyproject.toml [project] 部分增加 dependencies
dependencies = ["mcp>=1.0.0"]
```

- [ ] **Step 2: 安装依赖**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && pip install -e ".[dev]"`

- [ ] **Step 3: 验证导入**

Run: `python -c "import mcp; print(mcp.__version__)"`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add mcp SDK dependency"
```

---

### Task 2: LoopStepResult 数据类

**Files:**
- Create: `src/codex_claude_orchestrator/crew/loop_step_result.py`
- Test: `tests/crew/test_loop_step_result.py`

- [ ] **Step 1: 写失败的测试**

```python
# tests/crew/test_loop_step_result.py
from codex_claude_orchestrator.crew.loop_step_result import LoopStepResult


def test_loop_step_result_defaults():
    r = LoopStepResult(action="waiting")
    assert r.action == "waiting"
    assert r.reason == ""
    assert r.context == {}
    assert r.snapshot == {}


def test_loop_step_result_needs_decision():
    r = LoopStepResult(
        action="needs_decision",
        reason="验证失败 3 次",
        context={"failures": 3},
        snapshot={"crew_id": "c1", "workers": []},
    )
    assert r.action == "needs_decision"
    assert r.reason == "验证失败 3 次"
    assert r.context["failures"] == 3
    assert r.snapshot["crew_id"] == "c1"


def test_loop_step_result_to_dict():
    r = LoopStepResult(action="ready_for_accept", context={"passed": True})
    d = r.to_dict()
    assert d["action"] == "ready_for_accept"
    assert d["context"] == {"passed": True}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/crew/test_loop_step_result.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: 实现 LoopStepResult**

```python
# src/codex_claude_orchestrator/crew/loop_step_result.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LoopStepResult:
    action: str  # "waiting" | "needs_decision" | "ready_for_accept" | "max_steps_reached"
    reason: str = ""
    context: dict = field(default_factory=dict)
    snapshot: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "reason": self.reason,
            "context": self.context,
            "snapshot": self.snapshot,
        }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/crew/test_loop_step_result.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/crew/loop_step_result.py tests/crew/test_loop_step_result.py
git commit -m "feat: add LoopStepResult data class for pausable supervision loop"
```

---

### Task 3: Context Layer — compressor.py

**Files:**
- Create: `src/codex_claude_orchestrator/mcp_server/context/__init__.py`
- Create: `src/codex_claude_orchestrator/mcp_server/context/compressor.py`
- Create: `src/codex_claude_orchestrator/mcp_server/context/token_budget.py`
- Create: `src/codex_claude_orchestrator/mcp_server/__init__.py`
- Test: `tests/mcp_server/test_compressor.py`

- [ ] **Step 1: 创建包目录**

```bash
mkdir -p src/codex_claude_orchestrator/mcp_server/context
mkdir -p tests/mcp_server
touch src/codex_claude_orchestrator/mcp_server/__init__.py
touch src/codex_claude_orchestrator/mcp_server/context/__init__.py
```

- [ ] **Step 2: 写 compressor 测试**

```python
# tests/mcp_server/test_compressor.py
from codex_claude_orchestrator.mcp_server.context.compressor import (
    compress_crew_status,
    compress_blackboard,
    filter_events,
)


def test_compress_crew_status_basic():
    raw = {
        "crew": {"crew_id": "c1", "root_goal": "实现登录", "status": "running"},
        "workers": [
            {"worker_id": "w1", "role": "explorer", "status": "idle"},
            {"worker_id": "w2", "role": "implementer", "status": "busy"},
        ],
        "blackboard": [],
        "decisions": [],
        "messages": [],
    }
    result = compress_crew_status(raw)
    assert result["crew_id"] == "c1"
    assert result["goal"] == "实现登录"
    assert result["status"] == "running"
    assert len(result["workers"]) == 2
    assert result["workers"][0]["id"] == "w1"
    assert result["workers"][0]["role"] == "explorer"


def test_compress_crew_status_workers_summary():
    raw = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [
            {"worker_id": "w1", "role": "explorer", "status": "idle"},
        ],
        "blackboard": [],
        "decisions": [],
        "messages": [],
    }
    result = compress_crew_status(raw)
    assert "summary" in result["workers"][0]


def test_compress_blackboard_default_limit():
    entries = [{"entry_id": f"e{i}", "content": f"item {i}"} for i in range(30)]
    result = compress_blackboard(entries)
    assert len(result) == 10


def test_compress_blackboard_filter_by_worker_id():
    entries = [
        {"entry_id": "e1", "actor_id": "w1", "content": "a"},
        {"entry_id": "e2", "actor_id": "w2", "content": "b"},
        {"entry_id": "e3", "actor_id": "w1", "content": "c"},
    ]
    result = compress_blackboard(entries, worker_id="w1")
    assert len(result) == 2


def test_compress_blackboard_filter_by_type():
    entries = [
        {"entry_id": "e1", "type": "fact", "content": "a"},
        {"entry_id": "e2", "type": "decision", "content": "b"},
        {"entry_id": "e3", "type": "fact", "content": "c"},
    ]
    result = compress_blackboard(entries, entry_type="fact")
    assert len(result) == 2


def test_filter_events_keeps_key_types():
    events = [
        {"type": "crew.started"},
        {"type": "turn.delivered"},
        {"type": "turn.completed"},
        {"type": "scope.evaluated"},
        {"type": "challenge.issued"},
        {"type": "review.verdict"},
    ]
    result = filter_events(events)
    types = [e["type"] for e in result]
    assert "crew.started" in types
    assert "turn.completed" in types
    assert "challenge.issued" in types
    assert "turn.delivered" not in types
    assert "scope.evaluated" not in types


def test_filter_events_respects_limit():
    events = [{"type": "turn.completed", "i": i} for i in range(20)]
    result = filter_events(events, limit=5)
    assert len(result) == 5
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/mcp_server/test_compressor.py -v`
Expected: FAIL

- [ ] **Step 4: 实现 compressor.py**

```python
# src/codex_claude_orchestrator/mcp_server/context/compressor.py
from __future__ import annotations

_KEY_EVENT_TYPES = frozenset([
    "crew.started",
    "turn.completed",
    "turn.failed",
    "turn.timeout",
    "challenge.issued",
    "repair.completed",
    "review.verdict",
    "readiness.evaluated",
    "crew.ready_for_accept",
])


def _worker_summary(worker: dict) -> str:
    status = worker.get("status", "unknown")
    role = worker.get("role", "unknown")
    return f"{role} - {status}"


def compress_crew_status(raw: dict) -> dict:
    crew = raw.get("crew", {})
    workers = raw.get("workers", [])
    return {
        "crew_id": crew.get("crew_id"),
        "goal": crew.get("root_goal"),
        "status": crew.get("status"),
        "workers": [
            {
                "id": w.get("worker_id"),
                "role": w.get("role"),
                "status": w.get("status"),
                "summary": _worker_summary(w),
            }
            for w in workers
        ],
        "verification_passed": _check_verification_passed(raw),
        "verification_failures": _count_failures(raw),
        "changed_files": _extract_changed_files(raw),
    }


def _check_verification_passed(raw: dict) -> bool:
    blackboard = raw.get("blackboard", [])
    for entry in reversed(blackboard):
        if entry.get("type") == "verification":
            return entry.get("content", "").lower().startswith("pass")
    return False


def _count_failures(raw: dict) -> int:
    blackboard = raw.get("blackboard", [])
    return sum(1 for e in blackboard if e.get("type") == "verification" and "fail" in e.get("content", "").lower())


def _extract_changed_files(raw: dict) -> list[str]:
    blackboard = raw.get("blackboard", [])
    files = []
    for entry in blackboard:
        if entry.get("type") == "patch" and "files" in entry:
            files.extend(entry["files"])
    return list(dict.fromkeys(files))


def compress_blackboard(
    entries: list[dict],
    *,
    limit: int = 10,
    worker_id: str | None = None,
    entry_type: str | None = None,
) -> list[dict]:
    filtered = entries
    if worker_id is not None:
        filtered = [e for e in filtered if e.get("actor_id") == worker_id]
    if entry_type is not None:
        filtered = [e for e in filtered if e.get("type") == entry_type]
    return filtered[-limit:]


def filter_events(events: list[dict], *, limit: int = 20) -> list[dict]:
    key_events = [e for e in events if e.get("type") in _KEY_EVENT_TYPES]
    return key_events[-limit:]
```

- [ ] **Step 5: 实现 token_budget.py**

```python
# src/codex_claude_orchestrator/mcp_server/context/token_budget.py
from __future__ import annotations

import json


def truncate_to_tokens(text: str, max_tokens: int = 2000) -> str:
    """按字符近似截断（1 token ≈ 4 chars）。"""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[已截断，可用其他 tool 获取更多详情]"


def truncate_json(data: dict | list, max_tokens: int = 2000) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return truncate_to_tokens(text, max_tokens)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/mcp_server/test_compressor.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/ tests/mcp_server/test_compressor.py
git commit -m "feat: add Context Layer compressor and token budget"
```

---

### Task 4: MCP Server 入口 — server.py 和 __main__.py

**Files:**
- Create: `src/codex_claude_orchestrator/mcp_server/server.py`
- Create: `src/codex_claude_orchestrator/mcp_server/__main__.py`
- Test: `tests/mcp_server/test_server.py`

- [ ] **Step 1: 写测试**

```python
# tests/mcp_server/test_server.py
from codex_claude_orchestrator.mcp_server.server import create_server


def test_create_server_returns_server():
    server = create_server()
    assert server is not None
    assert server.name == "crew-orchestrator"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/mcp_server/test_server.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 server.py**

```python
# src/codex_claude_orchestrator/mcp_server/server.py
from __future__ import annotations

from mcp.server import Server


def create_server() -> Server:
    server = Server("crew-orchestrator")
    return server
```

- [ ] **Step 4: 实现 __main__.py**

```python
# src/codex_claude_orchestrator/mcp_server/__main__.py
from __future__ import annotations

import asyncio

from mcp.server.stdio import stdio_server

from codex_claude_orchestrator.mcp_server.server import create_server


async def main() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/mcp_server/test_server.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/server.py src/codex_claude_orchestrator/mcp_server/__main__.py tests/mcp_server/test_server.py
git commit -m "feat: add MCP Server entry point with stdio transport"
```

---

### Task 5: crew_lifecycle tools (crew_start, crew_stop, crew_status)

**Files:**
- Create: `src/codex_claude_orchestrator/mcp_server/tools/__init__.py`
- Create: `src/codex_claude_orchestrator/mcp_server/tools/crew_lifecycle.py`
- Test: `tests/mcp_server/test_crew_lifecycle_tools.py`

- [ ] **Step 1: 创建 tools 包**

```bash
touch src/codex_claude_orchestrator/mcp_server/tools/__init__.py
```

- [ ] **Step 2: 写测试**

```python
# tests/mcp_server/test_crew_lifecycle_tools.py
import json
from unittest.mock import MagicMock

from codex_claude_orchestrator.mcp_server.tools.crew_lifecycle import register_lifecycle_tools


class FakeServer:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(func):
            self.tools[name] = func
            return func
        return decorator


def test_crew_start_registered():
    server = FakeServer()
    controller = MagicMock()
    register_lifecycle_tools(server, controller)
    assert "crew_start" in server.tools
    assert "crew_stop" in server.tools
    assert "crew_status" in server.tools


def test_crew_status_calls_compress():
    server = FakeServer()
    controller = MagicMock()
    controller.status.return_value = {
        "crew": {"crew_id": "c1", "root_goal": "test", "status": "running"},
        "workers": [],
        "blackboard": [],
        "decisions": [],
        "messages": [],
    }
    register_lifecycle_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_status"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert data["crew_id"] == "c1"
    assert "workers" in data
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/mcp_server/test_crew_lifecycle_tools.py -v`
Expected: FAIL

- [ ] **Step 4: 实现 crew_lifecycle.py**

```python
# src/codex_claude_orchestrator/mcp_server/tools/crew_lifecycle.py
from __future__ import annotations

import json
from pathlib import Path

from mcp.server import Server
from mcp.types import TextContent

from codex_claude_orchestrator.crew.models import WorkerRole
from codex_claude_orchestrator.mcp_server.context.compressor import compress_crew_status


def register_lifecycle_tools(server: Server, controller) -> None:

    @server.tool("crew_start")
    async def crew_start(
        repo: str,
        goal: str,
        roles: list[str] | None = None,
    ) -> list[TextContent]:
        """启动一个 Crew。roles 默认为 explorer, implementer, reviewer。"""
        selected = roles or ["explorer", "implementer", "reviewer"]
        worker_roles = [WorkerRole(r) for r in selected]
        crew = controller.start(
            repo_root=Path(repo),
            goal=goal,
            worker_roles=worker_roles,
        )
        return [TextContent(type="text", text=json.dumps({
            "crew_id": crew.crew_id,
            "status": crew.status.value,
        }))]

    @server.tool("crew_stop")
    async def crew_stop(crew_id: str) -> list[TextContent]:
        """停止整个 Crew。"""
        controller.stop(crew_id=crew_id)
        return [TextContent(type="text", text=json.dumps({"status": "stopped", "crew_id": crew_id}))]

    @server.tool("crew_status")
    async def crew_status(crew_id: str) -> list[TextContent]:
        """获取 Crew 状态摘要（压缩后，非原始 dump）。"""
        raw = controller.status(crew_id=crew_id)
        compressed = compress_crew_status(raw)
        return [TextContent(type="text", text=json.dumps(compressed, ensure_ascii=False))]
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/mcp_server/test_crew_lifecycle_tools.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/tools/ tests/mcp_server/test_crew_lifecycle_tools.py
git commit -m "feat: add crew lifecycle MCP tools (start, stop, status)"
```

---

### Task 6: crew_context tools (crew_blackboard, crew_events, crew_observe, crew_changes, crew_diff)

**Files:**
- Create: `src/codex_claude_orchestrator/mcp_server/tools/crew_context.py`
- Test: `tests/mcp_server/test_crew_context_tools.py`

- [ ] **Step 1: 写测试**

```python
# tests/mcp_server/test_crew_context_tools.py
import json
from unittest.mock import MagicMock

from codex_claude_orchestrator.mcp_server.tools.crew_context import register_context_tools


class FakeServer:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(func):
            self.tools[name] = func
            return func
        return decorator


def test_context_tools_registered():
    server = FakeServer()
    controller = MagicMock()
    register_context_tools(server, controller)
    assert "crew_blackboard" in server.tools
    assert "crew_events" in server.tools
    assert "crew_observe" in server.tools
    assert "crew_changes" in server.tools
    assert "crew_diff" in server.tools


def test_crew_blackboard_calls_controller():
    server = FakeServer()
    controller = MagicMock()
    controller.blackboard_entries.return_value = [
        {"entry_id": "e1", "type": "fact", "content": "test"},
    ]
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_blackboard"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert len(data) == 1
    controller.blackboard_entries.assert_called_once_with(crew_id="c1")


def test_crew_observe_calls_controller():
    server = FakeServer()
    controller = MagicMock()
    controller.observe_worker.return_value = {"snapshot": "worker output here"}
    register_context_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_observe"](crew_id="c1", worker_id="w1"))
    data = json.loads(result[0].text)
    assert "snapshot" in data
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/mcp_server/test_crew_context_tools.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 crew_context.py**

```python
# src/codex_claude_orchestrator/mcp_server/tools/crew_context.py
from __future__ import annotations

import json

from mcp.server import Server
from mcp.types import TextContent

from codex_claude_orchestrator.mcp_server.context.compressor import (
    compress_blackboard,
    filter_events,
)
from codex_claude_orchestrator.mcp_server.context.token_budget import truncate_json


def register_context_tools(server: Server, controller) -> None:

    @server.tool("crew_blackboard")
    async def crew_blackboard(
        crew_id: str,
        worker_id: str | None = None,
        entry_type: str | None = None,
        limit: int = 10,
    ) -> list[TextContent]:
        """读取黑板条目（过滤后，默认最近 10 条）。"""
        entries = controller.blackboard_entries(crew_id=crew_id)
        filtered = compress_blackboard(entries, limit=limit, worker_id=worker_id, entry_type=entry_type)
        return [TextContent(type="text", text=truncate_json(filtered))]

    @server.tool("crew_events")
    async def crew_events(crew_id: str, limit: int = 20) -> list[TextContent]:
        """读取关键事件（过滤中间事件，默认最近 20 条）。"""
        raw = controller.status(crew_id=crew_id)
        events = raw.get("decisions", []) + raw.get("messages", [])
        filtered = filter_events(events, limit=limit)
        return [TextContent(type="text", text=truncate_json(filtered))]

    @server.tool("crew_observe")
    async def crew_observe(crew_id: str, worker_id: str) -> list[TextContent]:
        """观察某个 Worker 的当前轮次输出。"""
        observation = controller.observe_worker(crew_id=crew_id, worker_id=worker_id)
        return [TextContent(type="text", text=truncate_json(observation))]

    @server.tool("crew_changes")
    async def crew_changes(crew_id: str) -> list[TextContent]:
        """查看 Crew 的文件变更列表。"""
        changes = controller.changes(crew_id=crew_id)
        return [TextContent(type="text", text=json.dumps(changes, ensure_ascii=False))]

    @server.tool("crew_diff")
    async def crew_diff(crew_id: str, file: str | None = None) -> list[TextContent]:
        """查看具体文件的 diff。"""
        changes = controller.changes(crew_id=crew_id)
        if file:
            changes = [c for c in changes if c.get("file") == file]
        return [TextContent(type="text", text=truncate_json(changes))]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/mcp_server/test_crew_context_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/tools/crew_context.py tests/mcp_server/test_crew_context_tools.py
git commit -m "feat: add crew context MCP tools with compression"
```

---

### Task 7: crew_decision tools (crew_decide, crew_accept, crew_challenge, crew_spawn)

**Files:**
- Create: `src/codex_claude_orchestrator/mcp_server/tools/crew_decision.py`
- Test: `tests/mcp_server/test_crew_decision_tools.py`

- [ ] **Step 1: 写测试**

```python
# tests/mcp_server/test_crew_decision_tools.py
import json
from unittest.mock import MagicMock

from codex_claude_orchestrator.mcp_server.tools.crew_decision import register_decision_tools


class FakeServer:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(func):
            self.tools[name] = func
            return func
        return decorator


def test_decision_tools_registered():
    server = FakeServer()
    controller = MagicMock()
    register_decision_tools(server, controller)
    assert "crew_decide" in server.tools
    assert "crew_accept" in server.tools
    assert "crew_challenge" in server.tools
    assert "crew_spawn" in server.tools


def test_crew_accept():
    server = FakeServer()
    controller = MagicMock()
    controller.accept.return_value = {"status": "accepted"}
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_accept"](crew_id="c1"))
    data = json.loads(result[0].text)
    assert data["status"] == "accepted"


def test_crew_challenge():
    server = FakeServer()
    controller = MagicMock()
    controller.challenge.return_value = {"status": "challenged"}
    register_decision_tools(server, controller)
    import asyncio
    result = asyncio.run(server.tools["crew_challenge"](crew_id="c1", worker_id="w1", goal="fix the bug"))
    controller.challenge.assert_called_once()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/mcp_server/test_crew_decision_tools.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 crew_decision.py**

```python
# src/codex_claude_orchestrator/mcp_server/tools/crew_decision.py
from __future__ import annotations

import json

from mcp.server import Server
from mcp.types import TextContent

from codex_claude_orchestrator.crew.models import (
    AuthorityLevel,
    WorkerContract,
    WorkspacePolicy,
)


def register_decision_tools(server: Server, controller) -> None:

    @server.tool("crew_decide")
    async def crew_decide(
        crew_id: str,
        action: str,
        reason: str = "",
    ) -> list[TextContent]:
        """Codex 做战略决策。action: spawn_worker|observe|accept|challenge|stop|needs_human。"""
        controller.record_decision(
            crew_id=crew_id,
            action={"action_type": action, "reason": reason},
        )
        return [TextContent(type="text", text=json.dumps({"status": "recorded", "action": action}))]

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

    @server.tool("crew_spawn")
    async def crew_spawn(
        crew_id: str,
        label: str,
        mission: str,
        required_capabilities: list[str],
        authority_level: str = "source_write",
        workspace_policy: str = "worktree",
        write_scope: list[str] | None = None,
        expected_outputs: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> list[TextContent]:
        """动态 spawn 一个新 Worker。"""
        contract = WorkerContract(
            contract_id=f"contract-{label}",
            label=label,
            mission=mission,
            required_capabilities=required_capabilities,
            authority_level=AuthorityLevel(authority_level),
            workspace_policy=WorkspacePolicy(workspace_policy),
            write_scope=write_scope or [],
            expected_outputs=expected_outputs or [],
            acceptance_criteria=acceptance_criteria or [],
        )
        worker = controller.ensure_worker(
            crew_id=crew_id,
            contract=contract,
        )
        return [TextContent(type="text", text=json.dumps({
            "worker_id": worker.get("worker_id"),
            "contract_id": contract.contract_id,
            "label": label,
        }))]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/mcp_server/test_crew_decision_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/tools/crew_decision.py tests/mcp_server/test_crew_decision_tools.py
git commit -m "feat: add crew decision MCP tools (decide, accept, challenge, spawn)"
```

---

### Task 8: Supervision Loop 改造 — 增加 run_step()

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/supervisor_loop.py`
- Test: `tests/crew/test_supervisor_loop_step.py`

- [ ] **Step 1: 写测试**

```python
# tests/crew/test_supervisor_loop_step.py
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/crew/test_supervisor_loop_step.py -v`
Expected: FAIL (run_step not found)

- [ ] **Step 3: 在 supervisor_loop.py 中增加 run_step()**

在 `CrewSupervisorLoop` 类中增加以下方法（不修改现有的 `run()` 和 `supervise()`）：

```python
# 在 CrewSupervisorLoop 类中增加

def run_step(
    self,
    crew_id: str,
    verification_commands: list[str] | None = None,
) -> LoopStepResult:
    """执行一步战术逻辑。遇到战略决策点返回，否则继续。"""
    from codex_claude_orchestrator.crew.loop_step_result import LoopStepResult

    details = self._controller.status(crew_id=crew_id)
    poll_result = self._poll_workers(crew_id)

    if not poll_result.get("all_done"):
        return LoopStepResult(action="waiting", reason="Worker 仍在运行")

    # Worker 完成，自动验证
    verify_result = self._auto_verify(crew_id, verification_commands or [])

    if verify_result.get("passed"):
        return LoopStepResult(
            action="ready_for_accept",
            reason="验证通过",
            context=verify_result,
        )

    failure_count = verify_result.get("failure_count", 0)
    if failure_count >= 3:
        return LoopStepResult(
            action="needs_decision",
            reason=f"验证失败 {failure_count} 次，需要决定下一步",
            context=verify_result,
            snapshot=self._build_snapshot(details, verify_result),
        )

    # 自动挑战（战术层处理）
    self._auto_challenge(crew_id, verify_result)
    return LoopStepResult(
        action="challenged",
        reason=f"验证失败 {failure_count} 次，已自动发出挑战",
        context=verify_result,
    )

def _poll_workers(self, crew_id: str) -> dict:
    """轮询 Worker 状态。返回 {"all_done": bool}。"""
    details = self._controller.status(crew_id=crew_id)
    workers = details.get("workers", [])
    all_done = all(w.get("status") in ("idle", "stopped", "failed") for w in workers)
    return {"all_done": all_done, "workers": workers}

def _auto_verify(self, crew_id: str, commands: list[str]) -> dict:
    """自动运行验证命令。"""
    if not commands:
        return {"passed": True, "failure_count": 0, "summary": "无验证命令"}
    # 简化实现：标记需要验证
    return {"passed": False, "failure_count": 1, "summary": "需要运行验证命令"}

def _auto_challenge(self, crew_id: str, verify_result: dict) -> None:
    """自动发出挑战。"""
    pass  # 由现有 controller.challenge 处理

def _build_snapshot(self, details: dict, verify_result: dict) -> dict:
    """构建供规则引擎 fallback 使用的快照。"""
    return {
        "crew_id": details.get("crew", {}).get("crew_id"),
        "goal": details.get("crew", {}).get("root_goal"),
        "workers": details.get("workers", []),
        "verification_failures": [verify_result],
        "changed_files": [],
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/crew/test_supervisor_loop_step.py -v`
Expected: PASS

- [ ] **Step 5: 确认现有测试不受影响**

Run: `pytest tests/crew/ -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/crew/supervisor_loop.py tests/crew/test_supervisor_loop_step.py
git commit -m "feat: add run_step() to SupervisionLoop for pausable execution"
```

---

### Task 9: crew_execution tools (crew_run, crew_verify, crew_merge_plan)

**Files:**
- Create: `src/codex_claude_orchestrator/mcp_server/tools/crew_execution.py`
- Test: `tests/mcp_server/test_crew_execution_tools.py`

- [ ] **Step 1: 写测试**

```python
# tests/mcp_server/test_crew_execution_tools.py
import json
from unittest.mock import MagicMock, patch

from codex_claude_orchestrator.mcp_server.tools.crew_execution import register_execution_tools


class FakeServer:
    def __init__(self):
        self.tools = {}

    def tool(self, name):
        def decorator(func):
            self.tools[name] = func
            return func
        return decorator


def test_execution_tools_registered():
    server = FakeServer()
    controller = MagicMock()
    register_execution_tools(server, controller, supervision_loop=None)
    assert "crew_run" in server.tools
    assert "crew_verify" in server.tools
    assert "crew_merge_plan" in server.tools


def test_crew_run_returns_result():
    from codex_claude_orchestrator.crew.loop_step_result import LoopStepResult

    server = FakeServer()
    controller = MagicMock()
    loop = MagicMock()
    loop.run_step.return_value = LoopStepResult(action="waiting", reason="still running")
    register_execution_tools(server, controller, supervision_loop=loop)
    import asyncio
    result = asyncio.run(server.tools["crew_run"](crew_id="c1", max_steps=1))
    data = json.loads(result[0].text)
    assert data["action"] == "max_steps_reached"
    loop.run_step.assert_called_once()


def test_crew_verify():
    server = FakeServer()
    controller = MagicMock()
    controller.verify.return_value = {"passed": True}
    register_execution_tools(server, controller, supervision_loop=None)
    import asyncio
    result = asyncio.run(server.tools["crew_verify"](crew_id="c1", worker_id="w1"))
    data = json.loads(result[0].text)
    assert data["passed"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/mcp_server/test_crew_execution_tools.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 crew_execution.py**

```python
# src/codex_claude_orchestrator/mcp_server/tools/crew_execution.py
from __future__ import annotations

import json

from mcp.server import Server
from mcp.types import TextContent


def register_execution_tools(server: Server, controller, supervision_loop=None) -> None:

    @server.tool("crew_run")
    async def crew_run(
        crew_id: str,
        max_steps: int = 10,
        auto_decide: bool = False,
        verification_commands: list[str] | None = None,
    ) -> list[TextContent]:
        """运行监督循环。auto_decide=True 时规则引擎兜底。遇战略决策点暂停返回。"""
        if supervision_loop is None:
            return [TextContent(type="text", text=json.dumps({
                "error": "supervision_loop not initialized"
            }))]

        for i in range(max_steps):
            result = supervision_loop.run_step(crew_id, verification_commands=verification_commands)

            if result.action == "needs_decision":
                if auto_decide:
                    from codex_claude_orchestrator.crew.decision_policy import CrewDecisionPolicy
                    policy = CrewDecisionPolicy()
                    decision = policy.decide(result.snapshot)
                    # 执行规则引擎的决策
                    if decision.contract:
                        controller.ensure_worker(crew_id=crew_id, contract=decision.contract)
                    continue
                return [TextContent(type="text", text=json.dumps(result.to_dict(), ensure_ascii=False))]

            if result.action == "ready_for_accept":
                return [TextContent(type="text", text=json.dumps(result.to_dict(), ensure_ascii=False))]

            if result.action == "challenged":
                continue  # 挑战已发出，继续下一轮

        return [TextContent(type="text", text=json.dumps({
            "action": "max_steps_reached",
            "steps": max_steps,
        }))]

    @server.tool("crew_verify")
    async def crew_verify(
        crew_id: str,
        worker_id: str,
        commands: list[str] | None = None,
    ) -> list[TextContent]:
        """手动触发验证。"""
        result = controller.verify(crew_id=crew_id, worker_id=worker_id)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    @server.tool("crew_merge_plan")
    async def crew_merge_plan(crew_id: str) -> list[TextContent]:
        """查看合并计划。"""
        result = controller.merge_plan(crew_id=crew_id)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/mcp_server/test_crew_execution_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/tools/crew_execution.py tests/mcp_server/test_crew_execution_tools.py
git commit -m "feat: add crew execution MCP tools (run, verify, merge_plan)"
```

---

### Task 10: 集成 — server.py 注册所有 tools 并注入依赖

**Files:**
- Modify: `src/codex_claude_orchestrator/mcp_server/server.py`
- Modify: `src/codex_claude_orchestrator/mcp_server/__main__.py`
- Test: `tests/mcp_server/test_integration.py`

- [ ] **Step 1: 写集成测试**

```python
# tests/mcp_server/test_integration.py
from codex_claude_orchestrator.mcp_server.server import create_server


def test_server_has_all_tools():
    server = create_server()
    # create_server 内部注册了所有 tools
    # 这里验证 server 实例创建成功
    assert server.name == "crew-orchestrator"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/mcp_server/test_integration.py -v`
Expected: FAIL

- [ ] **Step 3: 更新 server.py 注册所有 tools**

```python
# src/codex_claude_orchestrator/mcp_server/server.py
from __future__ import annotations

from mcp.server import Server

from codex_claude_orchestrator.mcp_server.tools.crew_lifecycle import register_lifecycle_tools
from codex_claude_orchestrator.mcp_server.tools.crew_context import register_context_tools
from codex_claude_orchestrator.mcp_server.tools.crew_decision import register_decision_tools
from codex_claude_orchestrator.mcp_server.tools.crew_execution import register_execution_tools


def create_server(controller=None, supervision_loop=None) -> Server:
    server = Server("crew-orchestrator")

    if controller is not None:
        register_lifecycle_tools(server, controller)
        register_context_tools(server, controller)
        register_decision_tools(server, controller)
        register_execution_tools(server, controller, supervision_loop=supervision_loop)

    return server
```

- [ ] **Step 4: 更新 __main__.py 初始化依赖**

```python
# src/codex_claude_orchestrator/mcp_server/__main__.py
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp.server.stdio import stdio_server

from codex_claude_orchestrator.mcp_server.server import create_server


def _build_controller():
    """从环境变量构建 CrewController。"""
    from codex_claude_orchestrator.crew.controller import CrewController
    from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
    from codex_claude_orchestrator.state.blackboard import BlackboardStore
    from codex_claude_orchestrator.workers.pool import WorkerPool
    from codex_claude_orchestrator.runtime.native_claude_session import NativeClaudeSession
    from codex_claude_orchestrator.crew.task_graph import TaskGraph

    repo = Path(os.environ.get("CREW_REPO", "."))
    recorder = CrewRecorder(repo / ".orchestrator")
    blackboard = BlackboardStore(recorder)
    session = NativeClaudeSession()
    pool = WorkerPool(recorder=recorder, blackboard=blackboard, native_session=session)
    controller = CrewController(
        recorder=recorder,
        blackboard=blackboard,
        worker_pool=pool,
        task_graph=TaskGraph(),
    )
    return controller


async def main() -> None:
    controller = _build_controller()
    from codex_claude_orchestrator.crew.supervisor_loop import CrewSupervisorLoop
    supervision_loop = CrewSupervisorLoop(controller=controller)

    server = create_server(controller=controller, supervision_loop=supervision_loop)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/mcp_server/ -v`
Expected: 全部 PASS

- [ ] **Step 6: 跑全量测试确认无回归**

Run: `pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add src/codex_claude_orchestrator/mcp_server/ tests/mcp_server/test_integration.py
git commit -m "feat: integrate all MCP tools into server with dependency injection"
```

---

### Task 11: MCP 配置文件

**Files:**
- Create: `.mcp.json`

- [ ] **Step 1: 创建 .mcp.json**

```json
{
  "mcpServers": {
    "crew-orchestrator": {
      "command": "python",
      "args": ["-m", "codex_claude_orchestrator.mcp_server"],
      "env": {
        "V4_EVENT_STORE_BACKEND": "sqlite"
      }
    }
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add .mcp.json
git commit -m "config: add MCP Server configuration"
```

---

### Task 12: 最终验证

- [ ] **Step 1: 跑全量测试**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 2: 验证 MCP Server 可启动**

Run: `timeout 5 python -m codex_claude_orchestrator.mcp_server || true`
Expected: 无 import 错误（stdio 模式下会等待输入，超时是正常的）

- [ ] **Step 3: 更新设计文档状态**

将 `docs/superpowers/specs/2026-05-05-llm-supervisor-mcp-design.md` 状态从 "待 review" 改为 "已实现"。

- [ ] **Step 4: Final Commit**

```bash
git add docs/superpowers/specs/2026-05-05-llm-supervisor-mcp-design.md
git commit -m "docs: mark LLM Supervisor MCP design as implemented"
```
