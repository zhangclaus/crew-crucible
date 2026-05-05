# Codex-Managed Claude Crew V3 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 V3 MVP：Codex 可以管理多个可持续的原生 Claude Code CLI worker，并通过 git worktree、blackboard、worker send/observe/attach/tail/status、verification、changed files 和轻量 merge plan 完成可审计协作。

**Architecture:** V3 MVP 不替换 V1/V2，而是在原生 `claude` CLI 终端会话之上新增 crew 级控制层。第一版只做 star topology：Codex/CrewController 是唯一调度者，Claude workers 不直接互相通信，worker 产出统一写入 `.orchestrator/crews/<crew_id>/`；`ClaudeBridge` 保留为批处理/兼容后备，不作为 V3 主 worker runtime。

**Tech Stack:** Python 3.11+, stdlib (`argparse`, `dataclasses`, `enum`, `json`, `pathlib`, `subprocess`, `uuid`), pytest, git worktree, tmux-backed native Claude Code CLI sessions, existing `VerificationRunner`/`PolicyGate`

---

## Scope Check

本计划收敛到 V3 MVP，避免把成熟版能力提前塞进第一版。

MVP 包含：

- `explorer`、`implementer`、`reviewer` 三类 worker。
- 每个 worker 是一个独立、可 attach、可观察的原生 Claude Code CLI session。
- `crew worker send/observe/attach/tail/status` 可以继续和指定 worker 交互。
- `BlackboardStore` 记录 Codex 决策、worker 输出摘要、验证结果、风险和 change evidence。
- implementer 默认使用 `WorkspaceMode.WORKTREE`，在独立 git worktree 和 worker branch 中写代码。
- 写入 worker 创建前默认要求主 repo clean；`--allow-dirty-base` 才会保存 dirty base patch 并尝试应用到 worker worktree。
- `WorkerChangeRecorder` 基于 worker branch diff 记录 changed files。
- `MergeArbiter` 只生成只读 merge plan，不应用 patch。

MVP 明确不包含：

- `competitor` worker。
- 独立 `verifier` worker。
- 动态 `worker add/stop`。
- GUI UI 改造。
- 自动 merge 或 patch apply。
- copy-based fallback 只用于 non-git repo 或测试 fake，不作为默认 worker runtime。

## Execution Preconditions

开始实现前运行：

```bash
.venv/bin/python -m pytest -q
```

Expected: 当前测试通过。如果失败，先确认失败是否与 V3 无关；不要在 V3 任务里顺手重构无关模块。

每个任务完成后运行该任务的定向测试。最后运行完整回归：

```bash
.venv/bin/python -m pytest -q
```

## File Structure

新增文件：

- `src/codex_claude_orchestrator/crew_models.py`：V3 MVP enum/dataclass。
- `src/codex_claude_orchestrator/crew_recorder.py`：`.orchestrator/crews/<crew_id>/` 持久化。
- `src/codex_claude_orchestrator/blackboard.py`：append-only blackboard 读写过滤。
- `src/codex_claude_orchestrator/task_graph.py`：默认三角色 task graph。
- `src/codex_claude_orchestrator/worktree_manager.py`：git worktree 创建、dirty base 检查、branch diff changed-files。
- `src/codex_claude_orchestrator/native_claude_session.py`：tmux/PTY 原生 Claude Code CLI session start/send/observe/attach/tail/status。
- `src/codex_claude_orchestrator/worker_pool.py`：worker start/send/observe/attach/tail/status。
- `src/codex_claude_orchestrator/crew_controller.py`：crew start、worker 操作、verify、challenge、accept、changes、merge-plan。
- `src/codex_claude_orchestrator/crew_verification.py`：crew 级命令验证和 artifact 记录。
- `src/codex_claude_orchestrator/worker_change_recorder.py`：基于 worktree branch diff 或 fallback snapshot 记录 worker changed files。
- `src/codex_claude_orchestrator/merge_arbiter.py`：基于 recorded changes 生成只读 merge plan。

修改文件：

- `src/codex_claude_orchestrator/models.py`：增加 `WorkspaceMode.WORKTREE` 和 worktree allocation metadata。
- `src/codex_claude_orchestrator/cli.py`：增加 `crew` 命令族、`--allow-dirty-base` 和 `build_crew_controller()`。
- `tests/test_cli.py`：增加 crew CLI 测试。

新增测试：

- `tests/test_crew_models.py`
- `tests/test_crew_recorder.py`
- `tests/test_blackboard.py`
- `tests/test_task_graph.py`
- `tests/test_worktree_manager.py`
- `tests/test_native_claude_session.py`
- `tests/test_worker_pool.py`
- `tests/test_crew_controller.py`
- `tests/test_crew_verification.py`
- `tests/test_worker_change_recorder.py`
- `tests/test_merge_arbiter.py`

## Task 1: Crew Models

**Files:**
- Modify: `src/codex_claude_orchestrator/models.py`
- Create: `src/codex_claude_orchestrator/crew_models.py`
- Create: `tests/test_crew_models.py`

- [ ] **Step 1: Write failing model tests**

```python
# tests/test_crew_models.py
from pathlib import Path

from codex_claude_orchestrator.crew_models import (
    ActorType,
    BlackboardEntry,
    BlackboardEntryType,
    CrewRecord,
    CrewStatus,
    CrewTaskRecord,
    CrewTaskStatus,
    WorkerRecord,
    WorkerRole,
    WorkerStatus,
)
from codex_claude_orchestrator.models import WorkspaceMode


def test_crew_record_serializes_enums_paths_and_worker_ids():
    crew = CrewRecord(
        crew_id="crew-1",
        root_goal="Build V3 MVP",
        repo=Path("/repo"),
        status=CrewStatus.RUNNING,
        active_worker_ids=["worker-explorer"],
    )

    data = crew.to_dict()

    assert data["crew_id"] == "crew-1"
    assert data["repo"] == "/repo"
    assert data["status"] == "running"
    assert data["active_worker_ids"] == ["worker-explorer"]


def test_worker_task_blackboard_serialization_matches_mvp_schema():
    worker = WorkerRecord(
        worker_id="worker-implementer",
        crew_id="crew-1",
        role=WorkerRole.IMPLEMENTER,
        agent_profile="claude",
        native_session_id="native-1",
        terminal_session="crew-1-worker-implementer",
        terminal_pane="crew-1-worker-implementer:claude.0",
        transcript_artifact="workers/worker-implementer/transcript.txt",
        turn_marker="<<<CODEX_TURN_DONE>>>",
        bridge_id=None,
        workspace_mode=WorkspaceMode.WORKTREE,
        workspace_path=Path("/tmp/worktree"),
        workspace_allocation_artifact="workers/worker-implementer/allocation.json",
        status=WorkerStatus.RUNNING,
        assigned_task_ids=["task-implementer"],
    )
    task = CrewTaskRecord(
        task_id="task-implementer",
        crew_id="crew-1",
        title="Implement patch",
        instructions="Modify the worker worktree branch.",
        role_required=WorkerRole.IMPLEMENTER,
        status=CrewTaskStatus.ASSIGNED,
        owner_worker_id=worker.worker_id,
    )
    entry = BlackboardEntry(
        entry_id="entry-1",
        crew_id="crew-1",
        task_id=task.task_id,
        actor_type=ActorType.WORKER,
        actor_id=worker.worker_id,
        type=BlackboardEntryType.PATCH,
        content="Changed app.py in worker worktree.",
        evidence_refs=["app.py"],
        confidence=0.8,
    )

    assert worker.to_dict()["role"] == "implementer"
    assert worker.to_dict()["workspace_mode"] == "worktree"
    assert task.to_dict()["status"] == "assigned"
    assert entry.to_dict()["actor_type"] == "worker"
    assert entry.to_dict()["type"] == "patch"
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_crew_models.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `codex_claude_orchestrator.crew_models`.

- [ ] **Step 3: Add `WORKTREE` workspace mode**

Modify `src/codex_claude_orchestrator/models.py`:

```python
class WorkspaceMode(StrEnum):
    ISOLATED = "isolated"
    SHARED = "shared"
    READONLY = "readonly"
    WORKTREE = "worktree"
```

Extend `WorkspaceAllocation` with optional worktree metadata:

```python
@dataclass(slots=True)
class WorkspaceAllocation:
    workspace_id: str
    path: Path
    mode: WorkspaceMode
    writable: bool
    baseline_snapshot: dict[str, str] = field(default_factory=dict)
    branch: str = ""
    base_ref: str = ""
    base_patch_artifact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)
```

- [ ] **Step 4: Implement crew models**

Create `src/codex_claude_orchestrator/crew_models.py` with these public types:

```python
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.models import WorkspaceMode, utc_now


def _normalize(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {item.name: _normalize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {key: _normalize(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_normalize(inner) for inner in value]
    return value


class CrewStatus(StrEnum):
    PLANNING = "planning"
    RUNNING = "running"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"
    ACCEPTED = "accepted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerRole(StrEnum):
    EXPLORER = "explorer"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"


class WorkerStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    IDLE = "idle"
    FAILED = "failed"
    STOPPED = "stopped"


class CrewTaskStatus(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    SUBMITTED = "submitted"
    CHALLENGED = "challenged"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class BlackboardEntryType(StrEnum):
    FACT = "fact"
    CLAIM = "claim"
    QUESTION = "question"
    RISK = "risk"
    PATCH = "patch"
    VERIFICATION = "verification"
    REVIEW = "review"
    DECISION = "decision"


class ActorType(StrEnum):
    CODEX = "codex"
    WORKER = "worker"


@dataclass(slots=True)
class CrewRecord:
    crew_id: str
    root_goal: str
    repo: str | Path
    status: CrewStatus = CrewStatus.PLANNING
    planner_summary: str = ""
    max_workers: int = 3
    active_worker_ids: list[str] = field(default_factory=list)
    task_graph_path: str | Path = ""
    blackboard_path: str | Path = ""
    verification_summary: str = ""
    merge_summary: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    final_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class WorkerRecord:
    worker_id: str
    crew_id: str
    role: WorkerRole
    agent_profile: str
    native_session_id: str
    terminal_session: str
    terminal_pane: str
    transcript_artifact: str
    turn_marker: str
    workspace_mode: WorkspaceMode
    workspace_path: str | Path
    bridge_id: str | None = None
    workspace_allocation_artifact: str = ""
    write_scope: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    status: WorkerStatus = WorkerStatus.CREATED
    assigned_task_ids: list[str] = field(default_factory=list)
    last_seen_at: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class CrewTaskRecord:
    task_id: str
    crew_id: str
    title: str
    instructions: str
    role_required: WorkerRole
    status: CrewTaskStatus = CrewTaskStatus.PENDING
    owner_worker_id: str | None = None
    blocked_by: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class BlackboardEntry:
    entry_id: str
    crew_id: str
    task_id: str | None
    actor_type: ActorType
    actor_id: str
    type: BlackboardEntryType
    content: str
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)
```

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_crew_models.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/models.py src/codex_claude_orchestrator/crew_models.py tests/test_crew_models.py
git commit -m "feat: add crew v3 mvp models"
```

## Task 2: CrewRecorder And BlackboardStore

**Files:**
- Create: `src/codex_claude_orchestrator/crew_recorder.py`
- Create: `src/codex_claude_orchestrator/blackboard.py`
- Create: `tests/test_crew_recorder.py`
- Create: `tests/test_blackboard.py`

- [ ] **Step 1: Write failing persistence tests**

```python
# tests/test_crew_recorder.py
from pathlib import Path

from codex_claude_orchestrator.crew_models import CrewRecord, CrewStatus, CrewTaskRecord, WorkerRole
from codex_claude_orchestrator.crew_recorder import CrewRecorder


def test_crew_recorder_persists_crew_tasks_workers_artifacts_and_final_report(tmp_path: Path):
    recorder = CrewRecorder(tmp_path / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo="/repo")
    task = CrewTaskRecord(
        task_id="task-explorer",
        crew_id=crew.crew_id,
        title="Explore",
        instructions="Read only.",
        role_required=WorkerRole.EXPLORER,
    )

    crew_dir = recorder.start_crew(crew)
    recorder.write_tasks(crew.crew_id, [task])
    artifact = recorder.write_text_artifact(crew.crew_id, "workers/worker-1/allocation.json", "{}")
    recorder.finalize_crew(crew.crew_id, CrewStatus.ACCEPTED, "accepted")
    details = recorder.read_crew(crew.crew_id)

    assert crew_dir == tmp_path / ".orchestrator" / "crews" / "crew-1"
    assert artifact.name == "allocation.json"
    assert details["crew"]["status"] == "accepted"
    assert details["tasks"][0]["task_id"] == "task-explorer"
    assert details["artifacts"] == ["workers/worker-1/allocation.json"]
    assert recorder.latest_crew_id() == "crew-1"
```

```python
# tests/test_blackboard.py
from pathlib import Path

from codex_claude_orchestrator.blackboard import BlackboardStore
from codex_claude_orchestrator.crew_models import ActorType, BlackboardEntry, BlackboardEntryType, CrewRecord
from codex_claude_orchestrator.crew_recorder import CrewRecorder


def test_blackboard_appends_and_filters_entries(tmp_path: Path):
    recorder = CrewRecorder(tmp_path / ".orchestrator")
    recorder.start_crew(CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo="/repo"))
    blackboard = BlackboardStore(recorder)
    blackboard.append(
        BlackboardEntry(
            entry_id="entry-1",
            crew_id="crew-1",
            task_id="task-explorer",
            actor_type=ActorType.CODEX,
            actor_id="codex",
            type=BlackboardEntryType.DECISION,
            content="Start explorer first.",
            confidence=1.0,
        )
    )

    assert blackboard.list_entries("crew-1")[0]["entry_id"] == "entry-1"
    assert blackboard.list_entries("crew-1", entry_type=BlackboardEntryType.DECISION)[0]["content"] == "Start explorer first."
    assert blackboard.list_entries("crew-1", task_id="missing") == []
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_crew_recorder.py tests/test_blackboard.py -v
```

Expected: FAIL because `crew_recorder` and `blackboard` do not exist.

- [ ] **Step 3: Implement persistence APIs**

Implement `CrewRecorder` with these public methods:

- `start_crew(crew: CrewRecord) -> Path`
- `update_crew(crew_id: str, updates: dict) -> dict`
- `append_worker(crew_id: str, worker: WorkerRecord) -> None`
- `write_tasks(crew_id: str, tasks: list[CrewTaskRecord]) -> None`
- `append_blackboard(crew_id: str, entry: BlackboardEntry) -> None`
- `write_text_artifact(crew_id: str, artifact_name: str, content: str) -> Path`
- `read_text_artifact(crew_id: str, artifact_name: str) -> str`
- `finalize_crew(crew_id: str, status: CrewStatus, final_summary: str) -> None`
- `list_crews() -> list[dict]`
- `read_crew(crew_id: str) -> dict`
- `latest_crew_id() -> str | None`

Persist files under:

```text
.orchestrator/crews/<crew_id>/crew.json
.orchestrator/crews/<crew_id>/tasks.json
.orchestrator/crews/<crew_id>/workers.jsonl
.orchestrator/crews/<crew_id>/blackboard.jsonl
.orchestrator/crews/<crew_id>/artifacts/
.orchestrator/crews/latest
```

Implement `BlackboardStore` with:

```python
class BlackboardStore:
    def __init__(self, recorder: CrewRecorder):
        self._recorder = recorder

    def append(self, entry: BlackboardEntry) -> dict:
        self._recorder.append_blackboard(entry.crew_id, entry)
        return entry.to_dict()

    def list_entries(
        self,
        crew_id: str,
        *,
        entry_type: BlackboardEntryType | None = None,
        task_id: str | None = None,
        actor_id: str | None = None,
    ) -> list[dict]:
        entries = self._recorder.read_crew(crew_id)["blackboard"]
        if entry_type is not None:
            entries = [entry for entry in entries if entry["type"] == entry_type.value]
        if task_id is not None:
            entries = [entry for entry in entries if entry.get("task_id") == task_id]
        if actor_id is not None:
            entries = [entry for entry in entries if entry.get("actor_id") == actor_id]
        return entries
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_crew_recorder.py tests/test_blackboard.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/crew_recorder.py src/codex_claude_orchestrator/blackboard.py tests/test_crew_recorder.py tests/test_blackboard.py
git commit -m "feat: persist crew records and blackboard"
```

## Task 3: TaskGraphPlanner

**Files:**
- Create: `src/codex_claude_orchestrator/task_graph.py`
- Create: `tests/test_task_graph.py`

- [ ] **Step 1: Write failing task graph tests**

```python
# tests/test_task_graph.py
from codex_claude_orchestrator.crew_models import CrewTaskStatus, WorkerRole
from codex_claude_orchestrator.task_graph import TaskGraphPlanner


def test_default_graph_creates_mvp_role_tasks_with_dependencies():
    planner = TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}")

    tasks = planner.default_graph("crew-1", "Build V3 MVP", [WorkerRole.EXPLORER, WorkerRole.IMPLEMENTER, WorkerRole.REVIEWER])

    by_role = {task.role_required: task for task in tasks}
    assert by_role[WorkerRole.EXPLORER].depends_on == []
    assert by_role[WorkerRole.IMPLEMENTER].depends_on == ["task-explorer"]
    assert by_role[WorkerRole.REVIEWER].depends_on == ["task-implementer"]
    assert by_role[WorkerRole.REVIEWER].expected_outputs == ["review", "risks", "acceptance recommendation"]


def test_assign_task_sets_owner_and_status():
    planner = TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}")
    tasks = planner.default_graph("crew-1", "Build V3 MVP", [WorkerRole.EXPLORER])

    assigned = planner.assign(tasks, "task-explorer", "worker-explorer")

    assert assigned[0].owner_worker_id == "worker-explorer"
    assert assigned[0].status == CrewTaskStatus.ASSIGNED
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_graph.py -v
```

Expected: FAIL because `task_graph` does not exist.

- [ ] **Step 3: Implement planner**

Implement `TaskGraphPlanner.default_graph()` for only these MVP roles:

- `explorer`: read-only facts, risks, relevant files.
- `implementer`: isolated patch, changed files, verification notes.
- `reviewer`: review, risks, acceptance recommendation.

Implement `TaskGraphPlanner.assign(tasks, task_id, worker_id)` by setting `owner_worker_id`, `status=ASSIGNED`, and `updated_at`.

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_graph.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/task_graph.py tests/test_task_graph.py
git commit -m "feat: add crew task graph planner"
```

## Task 4: Worktree Manager, Native Claude Session, And WorkerPool Lifecycle

**Files:**
- Create: `src/codex_claude_orchestrator/worktree_manager.py`
- Create: `src/codex_claude_orchestrator/native_claude_session.py`
- Create: `src/codex_claude_orchestrator/worker_pool.py`
- Create: `tests/test_worktree_manager.py`
- Create: `tests/test_native_claude_session.py`
- Create: `tests/test_worker_pool.py`

- [ ] **Step 1: Write failing worktree manager tests**

```python
# tests/test_worktree_manager.py
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from codex_claude_orchestrator.models import WorkspaceMode
from codex_claude_orchestrator.worktree_manager import DirtyWorktreeError, WorktreeManager


class FakeGitRunner:
    def __init__(self, dirty_output: str = ""):
        self.dirty_output = dirty_output
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return CompletedProcess(command, 0, stdout="true\n", stderr="")
        if command[:2] == ["git", "status"]:
            return CompletedProcess(command, 0, stdout=self.dirty_output, stderr="")
        if command[:2] == ["git", "rev-parse"]:
            return CompletedProcess(command, 0, stdout="base-sha\n", stderr="")
        if command[:2] == ["git", "diff"]:
            return CompletedProcess(command, 0, stdout="src/app.py\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")


def test_worktree_manager_creates_branch_worktree_for_clean_repo(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    runner = FakeGitRunner()
    manager = WorktreeManager(
        state_root=tmp_path / ".orchestrator",
        runner=runner,
        branch_name_factory=lambda crew_id, worker_id: f"codex/{crew_id}-{worker_id}",
    )

    allocation = manager.prepare(repo_root=repo_root, crew_id="crew-1", worker_id="worker-implementer")
    changed = manager.changed_files(allocation)

    assert allocation.mode == WorkspaceMode.WORKTREE
    assert allocation.path == tmp_path / ".orchestrator" / "worktrees" / "crew-1" / "worker-implementer"
    assert allocation.branch == "codex/crew-1-worker-implementer"
    assert allocation.base_ref == "base-sha"
    assert changed == ["src/app.py"]
    assert ["git", "worktree", "add", "-b", "codex/crew-1-worker-implementer", str(allocation.path), "base-sha"] in [
        call[0] for call in runner.calls
    ]


def test_worktree_manager_blocks_dirty_repo_by_default(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    manager = WorktreeManager(
        state_root=tmp_path / ".orchestrator",
        runner=FakeGitRunner(dirty_output=" M app.py\n"),
    )

    with pytest.raises(DirtyWorktreeError, match="repo has uncommitted changes"):
        manager.prepare(repo_root=repo_root, crew_id="crew-1", worker_id="worker-implementer")
```

- [ ] **Step 2: Run failing worktree manager tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_worktree_manager.py -v
```

Expected: FAIL because `worktree_manager` does not exist.

- [ ] **Step 3: Implement `WorktreeManager`**

Create `src/codex_claude_orchestrator/worktree_manager.py` with:

- `DirtyWorktreeError`
- `NotGitRepositoryError`
- `WorktreeManager.prepare(repo_root, crew_id, worker_id, allow_dirty_base=False) -> WorkspaceAllocation`
- `WorktreeManager.changed_files(allocation) -> list[str]`

Rules:

- Check git repo with `git rev-parse --is-inside-work-tree`.
- Check dirty state with `git status --porcelain`.
- If dirty and `allow_dirty_base=False`, raise `DirtyWorktreeError` with dirty paths.
- If dirty and `allow_dirty_base=True`, write `git diff --binary HEAD` to `workers/<worker_id>/dirty-base.patch`, apply it inside the created worktree, and set `base_patch_artifact`.
- Resolve base with `git rev-parse HEAD`.
- Create branch name via `branch_name_factory(crew_id, worker_id)` or default `codex/<crew_id>-<worker_id>`.
- If branch creation fails because the branch already exists, append a short suffix and record the original requested branch in the allocation artifact.
- Create worktree at `<state_root>/worktrees/<crew_id>/<worker_id>` with `git worktree add -b <branch> <path> <base_ref>`.
- Return `WorkspaceAllocation(mode=WorkspaceMode.WORKTREE, writable=True, branch=branch, base_ref=base_ref)`.
- `changed_files()` runs `git diff --name-only <base_ref>...HEAD` inside the worktree.

- [ ] **Step 4: Run worktree manager tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_worktree_manager.py -v
```

Expected: PASS.

- [ ] **Step 5: Write failing native session tests**

```python
# tests/test_native_claude_session.py
from pathlib import Path
from subprocess import CompletedProcess

from codex_claude_orchestrator.native_claude_session import NativeClaudeSession


class FakeTmuxRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[:3] == ["tmux", "capture-pane", "-p"]:
            return CompletedProcess(command, 0, stdout="Claude is editing\n<<<CODEX_TURN_DONE status=ready_for_codex>>>\n", stderr="")
        if command[:3] == ["tmux", "has-session", "-t"]:
            return CompletedProcess(command, 0, stdout="", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")


def test_native_session_starts_claude_in_tmux_with_transcript_and_marker(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    transcript = tmp_path / "transcript.txt"
    runner = FakeTmuxRunner()
    session = NativeClaudeSession(
        tmux="tmux",
        runner=runner,
        session_name_factory=lambda worker_id: f"crew-1-{worker_id}",
        turn_marker="<<<CODEX_TURN_DONE status=ready_for_codex>>>",
    )

    started = session.start(
        repo_root=repo_root,
        worker_id="worker-explorer",
        role="explorer",
        instructions="Read only. Report facts and risks.",
        transcript_path=transcript,
    )
    sent = session.send(
        terminal_pane=started["terminal_pane"],
        message="Continue the investigation.",
    )
    observed = session.observe(terminal_pane=started["terminal_pane"], lines=200)
    tailed = session.tail(transcript_path=transcript, limit=20)
    status = session.status(terminal_session=started["terminal_session"])
    attached = session.attach(terminal_session=started["terminal_session"])

    commands = [call[0] for call in runner.calls]
    assert started == {
        "native_session_id": "crew-1-worker-explorer",
        "terminal_session": "crew-1-worker-explorer",
        "terminal_pane": "crew-1-worker-explorer:claude.0",
        "transcript_artifact": str(transcript),
        "turn_marker": "<<<CODEX_TURN_DONE status=ready_for_codex>>>",
    }
    assert any(command[:4] == ["tmux", "new-session", "-d", "-s"] for command in commands)
    assert any(command[:4] == ["tmux", "send-keys", "-t", "crew-1-worker-explorer:claude.0"] for command in commands)
    assert "Continue the investigation." in sent["message"]
    assert "<<<CODEX_TURN_DONE" in sent["message"]
    assert observed["marker_seen"] is True
    assert tailed["transcript_artifact"] == str(transcript)
    assert status["running"] is True
    assert attached["attach_command"] == "tmux attach -t crew-1-worker-explorer"
```

- [ ] **Step 6: Run failing native session tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_native_claude_session.py -v
```

Expected: FAIL because `native_claude_session` does not exist.

- [ ] **Step 7: Implement `NativeClaudeSession`**

Create `src/codex_claude_orchestrator/native_claude_session.py` with these public methods:

- `start(repo_root, worker_id, role, instructions, transcript_path) -> dict`
- `send(terminal_pane, message) -> dict`
- `observe(terminal_pane, lines=200) -> dict`
- `tail(transcript_path, limit=80) -> dict`
- `status(terminal_session) -> dict`
- `attach(terminal_session) -> dict`

Rules:

- Start a detached tmux session named by `session_name_factory(worker_id)`.
- Create one `claude` window and run native Claude Code CLI inside it.
- Wrap native `claude` with `script -q <transcript_path> claude` so the terminal transcript is preserved as an artifact.
- Send the initial prompt after the CLI starts; include role, workspace path, task instructions, and the required turn marker.
- `send()` appends `When this turn is complete, print exactly: <marker>` to every Codex instruction.
- `observe()` uses `tmux capture-pane -p -t <pane> -S -<lines>` and returns `marker_seen`.
- `tail()` reads the transcript artifact from disk when present and returns the last `limit` lines.
- `status()` uses `tmux has-session -t <session>` and returns `running`.
- `attach()` returns the exact command string `tmux attach -t <session>` and may also call tmux when the CLI command requests an interactive attach.

- [ ] **Step 8: Run native session tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_native_claude_session.py -v
```

Expected: PASS.

- [ ] **Step 9: Write failing WorkerPool lifecycle tests**

```python
# tests/test_worker_pool.py
from pathlib import Path

from codex_claude_orchestrator.blackboard import BlackboardStore
from codex_claude_orchestrator.crew_models import CrewRecord, CrewTaskRecord, WorkerRole
from codex_claude_orchestrator.crew_recorder import CrewRecorder
from codex_claude_orchestrator.models import WorkspaceAllocation, WorkspaceMode
from codex_claude_orchestrator.worker_pool import WorkerPool


class FakeWorktreeManager:
    def __init__(self):
        self.prepared = []

    def prepare(self, *, repo_root, crew_id, worker_id, allow_dirty_base=False):
        self.prepared.append(
            {
                "repo_root": repo_root,
                "crew_id": crew_id,
                "worker_id": worker_id,
                "allow_dirty_base": allow_dirty_base,
            }
        )
        path = repo_root.parent / ".orchestrator" / "worktrees" / crew_id / worker_id
        path.mkdir(parents=True, exist_ok=True)
        return WorkspaceAllocation(
            workspace_id=f"{crew_id}-{worker_id}",
            path=path,
            mode=WorkspaceMode.WORKTREE,
            writable=True,
            branch=f"codex/{crew_id}-{worker_id}",
            base_ref="base-sha",
        )


class FakeNativeSession:
    def __init__(self):
        self.starts = []
        self.sends = []
        self.observes = []

    def start(self, **kwargs):
        self.starts.append(kwargs)
        terminal_session = f"crew-1-{kwargs['worker_id']}"
        return {
            "native_session_id": f"native-{kwargs['worker_id']}",
            "terminal_session": terminal_session,
            "terminal_pane": f"{terminal_session}:claude.0",
            "transcript_artifact": str(kwargs["transcript_path"]),
            "turn_marker": "<<<CODEX_TURN_DONE status=ready_for_codex>>>",
        }

    def send(self, **kwargs):
        self.sends.append(kwargs)
        return {
            "message": kwargs["message"],
            "marker": "<<<CODEX_TURN_DONE status=ready_for_codex>>>",
            "marker_seen": True,
        }

    def observe(self, **kwargs):
        self.observes.append(kwargs)
        return {"snapshot": "Claude is editing", "marker_seen": False}

    def status(self, **kwargs):
        return {"running": True, "terminal_session": kwargs["terminal_session"]}

    def tail(self, **kwargs):
        return {"transcript_artifact": str(kwargs["transcript_path"]), "lines": ["started"]}

    def attach(self, **kwargs):
        return {"attach_command": f"tmux attach -t {kwargs['terminal_session']}"}


def test_worker_pool_starts_implementer_in_worktree_and_records_allocation(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("print('one')\n", encoding="utf-8")
    recorder = CrewRecorder(repo_root / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo=repo_root)
    recorder.start_crew(crew)
    fake_native = FakeNativeSession()
    fake_worktree = FakeWorktreeManager()
    pool = WorkerPool(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        worktree_manager=fake_worktree,
        native_session=fake_native,
        worker_id_factory=lambda role: f"worker-{role.value}",
        entry_id_factory=lambda: "entry-worker-started",
    )
    task = CrewTaskRecord(
        task_id="task-implementer",
        crew_id=crew.crew_id,
        title="Implement",
        instructions="Modify app.py.",
        role_required=WorkerRole.IMPLEMENTER,
    )

    worker = pool.start_worker(repo_root=repo_root, crew=crew, task=task)

    assert worker.workspace_mode == WorkspaceMode.WORKTREE
    assert Path(worker.workspace_path) != repo_root
    assert worker.workspace_allocation_artifact == "workers/worker-implementer/allocation.json"
    assert worker.native_session_id == "native-worker-implementer"
    assert worker.terminal_pane == "crew-1-worker-implementer:claude.0"
    assert "transcript.txt" in worker.transcript_artifact
    assert fake_native.starts[0]["repo_root"] == Path(worker.workspace_path)
    assert fake_native.starts[0]["role"] == "implementer"
    assert fake_worktree.prepared[0]["worker_id"] == "worker-implementer"
    assert fake_worktree.prepared[0]["allow_dirty_base"] is False
    assert recorder.read_crew(crew.crew_id)["workers"][0]["native_session_id"] == "native-worker-implementer"


def test_worker_pool_can_send_observe_attach_tail_and_status_existing_worker(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo=repo_root)
    recorder.start_crew(crew)
    fake_native = FakeNativeSession()
    fake_worktree = FakeWorktreeManager()
    pool = WorkerPool(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        worktree_manager=fake_worktree,
        native_session=fake_native,
        worker_id_factory=lambda role: f"worker-{role.value}",
        entry_id_factory=lambda: "entry-worker",
    )
    task = CrewTaskRecord(
        task_id="task-explorer",
        crew_id=crew.crew_id,
        title="Explore",
        instructions="Read only.",
        role_required=WorkerRole.EXPLORER,
    )
    worker = pool.start_worker(repo_root=repo_root, crew=crew, task=task)

    sent = pool.send_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id=worker.worker_id, message="continue")
    observed = pool.observe_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id=worker.worker_id, lines=120)
    attached = pool.attach_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id=worker.worker_id)
    tail = pool.tail_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id=worker.worker_id, limit=5)
    status = pool.status_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id=worker.worker_id)

    assert sent["marker_seen"] is True
    assert observed["snapshot"] == "Claude is editing"
    assert attached["attach_command"] == "tmux attach -t crew-1-worker-explorer"
    assert tail["lines"] == ["started"]
    assert status["running"] is True
    assert fake_native.sends[0]["terminal_pane"] == "crew-1-worker-explorer:claude.0"
```

- [ ] **Step 10: Run failing WorkerPool tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker_pool.py -v
```

Expected: FAIL because `worker_pool` does not exist.

- [ ] **Step 11: Implement `WorkerPool` public API**

Create `src/codex_claude_orchestrator/worker_pool.py` with:

- `start_worker(repo_root, crew, task, allow_dirty_base=False) -> WorkerRecord`
- `send_worker(repo_root, crew_id, worker_id, message) -> dict`
- `observe_worker(repo_root, crew_id, worker_id, lines) -> dict`
- `attach_worker(repo_root, crew_id, worker_id) -> dict`
- `tail_worker(repo_root, crew_id, worker_id, limit) -> dict`
- `status_worker(repo_root, crew_id, worker_id) -> dict`

Rules:

- `explorer` and `reviewer` use `WorkspaceMode.READONLY` and start native Claude in the repo root with read-only role instructions.
- `implementer` uses `WorkspaceMode.WORKTREE`, allocates a branch/worktree through `WorktreeManager`, and starts native Claude inside that worktree.
- `allow_dirty_base` defaults to `False`; propagate it to `WorktreeManager.prepare()` from `CrewController.start()`.
- `start_worker()` writes allocation JSON to `workers/<worker_id>/allocation.json`.
- `start_worker()` writes transcript to `workers/<worker_id>/transcript.txt`.
- `send_worker()` appends a `decision` entry before send and a `claim` entry after send with marker/evidence fields from the native session response.
- `observe_worker()` captures the current tmux pane snapshot and records no blackboard entry unless the caller explicitly challenges or accepts.
- `attach_worker()` returns the native attach command for a human-visible Claude Code CLI session.
- Worker lookup reads `recorder.read_crew(crew_id)["workers"]` and matches by `worker_id`.

- [ ] **Step 12: Run tests and regressions**

Run:

```bash
.venv/bin/python -m pytest tests/test_worktree_manager.py tests/test_native_claude_session.py tests/test_worker_pool.py tests/test_tmux_console.py tests/test_workspace_manager.py -q
```

Expected: PASS.

- [ ] **Step 13: Commit**

```bash
git add src/codex_claude_orchestrator/worktree_manager.py src/codex_claude_orchestrator/native_claude_session.py src/codex_claude_orchestrator/worker_pool.py tests/test_worktree_manager.py tests/test_native_claude_session.py tests/test_worker_pool.py
git commit -m "feat: manage native claude crew workers"
```

## Task 5: CrewController And Crew CLI

**Files:**
- Create: `src/codex_claude_orchestrator/crew_controller.py`
- Modify: `src/codex_claude_orchestrator/cli.py`
- Create: `tests/test_crew_controller.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing controller and CLI tests**

```python
# tests/test_crew_controller.py
from pathlib import Path

from codex_claude_orchestrator.blackboard import BlackboardStore
from codex_claude_orchestrator.crew_controller import CrewController
from codex_claude_orchestrator.crew_models import CrewStatus, WorkerRole
from codex_claude_orchestrator.crew_recorder import CrewRecorder
from codex_claude_orchestrator.task_graph import TaskGraphPlanner


class FakeWorkerPool:
    def __init__(self):
        self.started = []
        self.sent = []
        self.observed = []
        self.attached = []

    def start_worker(self, *, repo_root, crew, task, allow_dirty_base=False):
        self.started.append((repo_root, crew.crew_id, task.task_id, task.role_required, allow_dirty_base))
        return type("Worker", (), {"worker_id": f"worker-{task.role_required.value}"})()

    def send_worker(self, **kwargs):
        self.sent.append(kwargs)
        return {"message": kwargs["message"], "marker_seen": True}

    def observe_worker(self, **kwargs):
        self.observed.append(kwargs)
        return {"snapshot": "Claude is reading files", "marker_seen": False}

    def attach_worker(self, **kwargs):
        self.attached.append(kwargs)
        return {"attach_command": "tmux attach -t crew-1-worker-explorer"}

    def tail_worker(self, **kwargs):
        return {"lines": ["worker transcript line"]}

    def status_worker(self, **kwargs):
        return {"running": True, "terminal_session": "crew-1-worker-explorer"}


def test_controller_starts_crew_and_delegates_worker_terminal_commands(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    pool = FakeWorkerPool()
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}"),
        worker_pool=pool,
        crew_id_factory=lambda: "crew-1",
        entry_id_factory=lambda: "entry-created",
    )

    crew = controller.start(
        repo_root=repo_root,
        goal="Build V3 MVP",
        worker_roles=[WorkerRole.EXPLORER, WorkerRole.IMPLEMENTER],
        allow_dirty_base=False,
    )
    sent = controller.send_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id="worker-explorer", message="continue")
    observed = controller.observe_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id="worker-explorer", lines=120)
    attached = controller.attach_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id="worker-explorer")
    tail = controller.tail_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id="worker-explorer", limit=5)
    status = controller.status_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id="worker-explorer")

    assert crew.status == CrewStatus.RUNNING
    assert crew.active_worker_ids == ["worker-explorer", "worker-implementer"]
    assert pool.started[1][4] is False
    assert sent["marker_seen"] is True
    assert observed["snapshot"] == "Claude is reading files"
    assert attached["attach_command"] == "tmux attach -t crew-1-worker-explorer"
    assert tail["lines"] == ["worker transcript line"]
    assert status["running"] is True
```

Append to `tests/test_cli.py`:

```python
def test_build_parser_exposes_crew_start_and_worker_commands():
    from codex_claude_orchestrator.cli import build_parser

    parser = build_parser()
    subparsers_action = next(action for action in parser._actions if action.dest == "command")

    assert "crew" in subparsers_action.choices
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_crew_controller.py tests/test_cli.py::test_build_parser_exposes_crew_start_and_worker_commands -v
```

Expected: FAIL because `crew_controller` and crew CLI do not exist.

- [ ] **Step 3: Implement `CrewController`**

Public methods:

- `start(repo_root, goal, worker_roles, allow_dirty_base=False) -> CrewRecord`
- `send_worker(repo_root, crew_id, worker_id, message) -> dict`
- `observe_worker(repo_root, crew_id, worker_id, lines) -> dict`
- `attach_worker(repo_root, crew_id, worker_id) -> dict`
- `tail_worker(repo_root, crew_id, worker_id, limit) -> dict`
- `status_worker(repo_root, crew_id, worker_id) -> dict`

`start()` behavior:

- Create `CrewRecord`.
- Write initial `decision` blackboard entry.
- Create tasks via `TaskGraphPlanner.default_graph()`.
- Start one worker per task through `WorkerPool.start_worker(..., allow_dirty_base=allow_dirty_base)`.
- Assign each task to its worker.
- Persist tasks and update crew status to `running`.

- [ ] **Step 4: Add CLI commands**

Add top-level `crew` commands:

```bash
orchestrator crew start --repo ... --goal ... --workers explorer,implementer,reviewer [--allow-dirty-base]
orchestrator crew status --repo ... --crew ...
orchestrator crew blackboard --repo ... --crew ...
orchestrator crew worker send --repo ... --crew ... --worker ... --message ...
orchestrator crew worker observe --repo ... --crew ... --worker ... --lines 200
orchestrator crew worker attach --repo ... --crew ... --worker ...
orchestrator crew worker tail --repo ... --crew ... --worker ... --limit 5
orchestrator crew worker status --repo ... --crew ... --worker ...
orchestrator crew supervise --repo ... --crew ... --verification-command ".venv/bin/python -m pytest -q"
```

Add helpers:

- `build_crew_controller(repo_root: Path) -> CrewController`
- `parse_worker_roles(value: str) -> list[WorkerRole]`

Route `crew worker send/observe/attach/tail/status` through `CrewController`.

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_crew_controller.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/crew_controller.py src/codex_claude_orchestrator/cli.py tests/test_crew_controller.py tests/test_cli.py
git commit -m "feat: add crew controller and worker cli"
```

## Task 6: Crew Verification, Challenge, And Accept

**Files:**
- Create: `src/codex_claude_orchestrator/crew_verification.py`
- Modify: `src/codex_claude_orchestrator/crew_controller.py`
- Modify: `src/codex_claude_orchestrator/cli.py`
- Create: `tests/test_crew_verification.py`
- Modify: `tests/test_crew_controller.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing verification test**

```python
# tests/test_crew_verification.py
from pathlib import Path
from subprocess import CompletedProcess

from codex_claude_orchestrator.crew_models import CrewRecord
from codex_claude_orchestrator.crew_recorder import CrewRecorder
from codex_claude_orchestrator.crew_verification import CrewVerificationRunner
from codex_claude_orchestrator.policy_gate import PolicyGate


def test_crew_verification_records_command_artifacts_and_blackboard_entry(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    recorder.start_crew(CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo=repo_root))
    runner = CrewVerificationRunner(
        repo_root=repo_root,
        recorder=recorder,
        policy_gate=PolicyGate(),
        command_runner=lambda argv, **kwargs: CompletedProcess(argv, 0, stdout="ok\n", stderr=""),
        verification_id_factory=lambda: "verification-1",
        entry_id_factory=lambda: "entry-verification",
    )

    result = runner.run("crew-1", "pytest -q")

    details = recorder.read_crew("crew-1")
    assert result["passed"] is True
    assert details["blackboard"][0]["type"] == "verification"
    assert details["artifacts"] == [
        "verification/verification-1/stderr.txt",
        "verification/verification-1/stdout.txt",
    ]
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_crew_verification.py -v
```

Expected: FAIL because `crew_verification` does not exist.

- [ ] **Step 3: Implement verification**

Implement `CrewVerificationRunner.run(crew_id, command)`:

- Use `shlex.split(command)`.
- Use `PolicyGate.guard_command(argv)`.
- Write stdout/stderr artifacts under `verification/<verification_id>/`.
- Append `BlackboardEntry(type=VERIFICATION, actor_id="codex")`.
- Return dict with `verification_id`, `command`, `passed`, `exit_code`, `summary`, `stdout_artifact`, `stderr_artifact`.

- [ ] **Step 4: Extend controller and CLI**

Controller methods:

- `verify(crew_id, command) -> dict`
- `challenge(crew_id, summary, task_id=None) -> dict`
- `accept(crew_id, summary) -> dict`

CLI commands:

```bash
orchestrator crew verify --repo ... --crew ... --command ...
orchestrator crew challenge --repo ... --crew ... --task ... --summary ...
orchestrator crew accept --repo ... --crew ... --summary ...
```

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_crew_verification.py tests/test_crew_controller.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/crew_verification.py src/codex_claude_orchestrator/crew_controller.py src/codex_claude_orchestrator/cli.py tests/test_crew_verification.py tests/test_crew_controller.py tests/test_cli.py
git commit -m "feat: add crew verification and decisions"
```

## Task 7: WorkerChangeRecorder

**Files:**
- Create: `src/codex_claude_orchestrator/worker_change_recorder.py`
- Modify: `src/codex_claude_orchestrator/crew_controller.py`
- Modify: `src/codex_claude_orchestrator/cli.py`
- Create: `tests/test_worker_change_recorder.py`
- Modify: `tests/test_crew_controller.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing changed-files test**

```python
# tests/test_worker_change_recorder.py
import json
from pathlib import Path

from codex_claude_orchestrator.crew_models import CrewRecord
from codex_claude_orchestrator.crew_recorder import CrewRecorder
from codex_claude_orchestrator.models import WorkspaceAllocation, WorkspaceMode
from codex_claude_orchestrator.worker_change_recorder import WorkerChangeRecorder


class FakeWorktreeManager:
    def __init__(self):
        self.changed_calls = []

    def changed_files(self, allocation):
        self.changed_calls.append(allocation)
        return ["src/app.py"]


def test_worker_change_recorder_detects_changes_from_worktree_branch(tmp_path: Path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    recorder = CrewRecorder(tmp_path / ".orchestrator")
    recorder.start_crew(CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo="/repo"))
    allocation = WorkspaceAllocation(
        workspace_id="crew-1-worker-implementer",
        path=worktree,
        mode=WorkspaceMode.WORKTREE,
        writable=True,
        branch="codex/crew-1-worker-implementer",
        base_ref="base-sha",
    )
    recorder.write_text_artifact(
        "crew-1",
        "workers/worker-implementer/allocation.json",
        json.dumps(allocation.to_dict(), ensure_ascii=False),
    )

    changes = WorkerChangeRecorder(recorder, worktree_manager=FakeWorktreeManager()).record_changes(
        "crew-1", "worker-implementer", allocation
    )

    assert changes["worker_id"] == "worker-implementer"
    assert changes["branch"] == "codex/crew-1-worker-implementer"
    assert changes["changed_files"] == ["src/app.py"]
    assert recorder.read_crew("crew-1")["blackboard"][0]["type"] == "patch"
```

- [ ] **Step 2: Run failing test**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker_change_recorder.py -v
```

Expected: FAIL because `worker_change_recorder` does not exist.

- [ ] **Step 3: Implement change recorder**

Implement:

```python
class WorkerChangeRecorder:
    def __init__(
        self,
        recorder: CrewRecorder,
        worktree_manager: WorktreeManager,
        entry_id_factory: Callable[[], str] | None = None,
    ):
        ...

    def record_changes(self, crew_id: str, worker_id: str, allocation: WorkspaceAllocation) -> dict:
        ...
```

Implementation details:

- For `WorkspaceMode.WORKTREE`, call `worktree_manager.changed_files(allocation)`.
- For fallback `WorkspaceMode.ISOLATED`, keep snapshot comparison as a compatibility path.
- Write `workers/<worker_id>/changes.json`.
- Append `BlackboardEntry(type=PATCH, actor_type=WORKER, actor_id=worker_id)` with branch/base_ref evidence.
- Return `{"crew_id": crew_id, "worker_id": worker_id, "branch": allocation.branch, "base_ref": allocation.base_ref, "changed_files": changed_files, "artifact": artifact_name}`.

- [ ] **Step 4: Extend controller and CLI**

Controller:

- `changes(crew_id, worker_id) -> dict`
- It reads the worker record, reads allocation artifact, reconstructs `WorkspaceAllocation`, and calls `WorkerChangeRecorder`.

CLI:

```bash
orchestrator crew changes --repo ... --crew ... --worker ...
```

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_worker_change_recorder.py tests/test_crew_controller.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/worker_change_recorder.py src/codex_claude_orchestrator/crew_controller.py src/codex_claude_orchestrator/cli.py tests/test_worker_change_recorder.py tests/test_crew_controller.py tests/test_cli.py
git commit -m "feat: record crew worker changes"
```

## Task 8: Lightweight Merge Plan

**Files:**
- Create: `src/codex_claude_orchestrator/merge_arbiter.py`
- Modify: `src/codex_claude_orchestrator/crew_controller.py`
- Modify: `src/codex_claude_orchestrator/cli.py`
- Create: `tests/test_merge_arbiter.py`
- Modify: `tests/test_crew_controller.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing merge plan tests**

```python
# tests/test_merge_arbiter.py
from codex_claude_orchestrator.merge_arbiter import MergeArbiter


def test_merge_arbiter_detects_overlapping_changed_files():
    plan = MergeArbiter().build_plan(
        "crew-1",
        changed_files_by_worker={
            "worker-a": ["src/app.py"],
            "worker-b": ["src/app.py"],
        },
    )

    assert plan["can_merge"] is False
    assert plan["conflicts"] == [{"path": "src/app.py", "workers": ["worker-a", "worker-b"]}]
    assert plan["recommendation"] == "requires_codex_decision"


def test_merge_arbiter_allows_non_overlapping_changed_files():
    plan = MergeArbiter().build_plan(
        "crew-1",
        changed_files_by_worker={
            "worker-a": ["src/app.py"],
            "worker-b": ["tests/test_app.py"],
        },
    )

    assert plan["can_merge"] is True
    assert plan["conflicts"] == []
    assert plan["recommendation"] == "ready_for_codex_review"
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_merge_arbiter.py -v
```

Expected: FAIL because `merge_arbiter` does not exist.

- [ ] **Step 3: Implement merge arbiter**

Implement `MergeArbiter.build_plan(crew_id, changed_files_by_worker)`:

- Build path -> worker owners map.
- Conflict if one path is changed by more than one worker.
- Return `crew_id`, `can_merge`, `conflicts`, `changed_files_by_worker`, `recommendation`.

- [ ] **Step 4: Extend controller and CLI**

Controller:

- `merge_plan(crew_id) -> dict`
- Read `workers/<worker_id>/changes.json` artifacts when present.
- Generate plan with `MergeArbiter`.
- Update `crew.merge_summary`.
- Write `merge_plan.json` artifact.

CLI:

```bash
orchestrator crew merge-plan --repo ... --crew ...
```

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_merge_arbiter.py tests/test_crew_controller.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/merge_arbiter.py src/codex_claude_orchestrator/crew_controller.py src/codex_claude_orchestrator/cli.py tests/test_merge_arbiter.py tests/test_crew_controller.py tests/test_cli.py
git commit -m "feat: add crew merge plan"
```

## Task 9: End-To-End Fake Flow And Regression

**Files:**
- Modify: `tests/test_crew_controller.py`
- Modify: `tests/test_cli.py` only if CLI regressions require assertion updates

- [ ] **Step 1: Add fake end-to-end controller test**

Append to `tests/test_crew_controller.py`:

```python
def test_crew_controller_fake_flow_start_send_verify_challenge_accept(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    pool = FakeWorkerPool()

    class FakeVerificationRunner:
        def run(self, crew_id, command):
            return {
                "verification_id": "verification-1",
                "command": command,
                "passed": True,
                "summary": "command passed: exit code 0",
            }

    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}"),
        worker_pool=pool,
        verification_runner=FakeVerificationRunner(),
        crew_id_factory=lambda: "crew-1",
        entry_id_factory=lambda: "entry-flow",
    )

    crew = controller.start(repo_root=repo_root, goal="Build V3 MVP", worker_roles=[WorkerRole.EXPLORER])
    sent = controller.send_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id="worker-explorer", message="continue")
    verification = controller.verify(crew_id=crew.crew_id, command="pytest -q")
    challenge = controller.challenge(crew_id=crew.crew_id, summary="Need more evidence", task_id="task-explorer")
    accepted = controller.accept(crew_id=crew.crew_id, summary="accepted with evidence")

    assert sent["marker_seen"] is True
    assert verification["passed"] is True
    assert challenge["type"] == "risk"
    assert accepted["status"] == "accepted"
```

- [ ] **Step 2: Run V3 focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_crew_models.py tests/test_crew_recorder.py tests/test_blackboard.py tests/test_task_graph.py tests/test_worktree_manager.py tests/test_native_claude_session.py tests/test_worker_pool.py tests/test_crew_controller.py tests/test_crew_verification.py tests/test_worker_change_recorder.py tests/test_merge_arbiter.py -q
```

Expected: PASS.

- [ ] **Step 3: Run V1/V2 regression tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_supervisor.py tests/test_session_engine.py tests/test_claude_bridge.py tests/test_cli.py tests/test_workspace_manager.py tests/test_policy_gate.py -q
```

Expected: PASS.

- [ ] **Step 4: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit final regression test**

```bash
git add tests/test_crew_controller.py tests/test_cli.py
git commit -m "test: cover crew v3 mvp flow"
```

## Manual Smoke Test

Use a small disposable repo before running against real work:

```bash
.venv/bin/orchestrator crew start --repo /path/to/repo --goal "Inspect this repo and propose one safe improvement" --workers explorer,implementer
.venv/bin/orchestrator crew status --repo /path/to/repo --crew <crew_id>
.venv/bin/orchestrator crew worker observe --repo /path/to/repo --crew <crew_id> --worker worker-explorer
.venv/bin/orchestrator crew worker attach --repo /path/to/repo --crew <crew_id> --worker worker-explorer
.venv/bin/orchestrator crew worker tail --repo /path/to/repo --crew <crew_id> --worker worker-explorer
.venv/bin/orchestrator crew worker send --repo /path/to/repo --crew <crew_id> --worker worker-explorer --message "Summarize the highest-confidence finding"
.venv/bin/orchestrator crew blackboard --repo /path/to/repo --crew <crew_id>
.venv/bin/orchestrator crew verify --repo /path/to/repo --crew <crew_id> --command ".venv/bin/python -m pytest -q"
.venv/bin/orchestrator crew accept --repo /path/to/repo --crew <crew_id> --summary "accepted after verification"
```

Expected:

- `.orchestrator/crews/<crew_id>/crew.json` exists.
- `.orchestrator/crews/<crew_id>/tasks.json` exists.
- `.orchestrator/crews/<crew_id>/workers.jsonl` exists.
- `.orchestrator/crews/<crew_id>/blackboard.jsonl` contains decision, claim, and verification entries.
- `git worktree list` shows a worker worktree for the implementer.
- The implementer worker record has `workspace_mode=worktree`, `branch`, and `base_ref` in its allocation artifact.
- `crew worker observe` returns the current native Claude terminal pane snapshot.
- `crew worker attach` prints or executes `tmux attach -t <terminal_session>`.
- `crew worker tail` returns the worker transcript lines.
- `crew status` returns `crew`, `tasks`, `workers`, `blackboard`, `final_report`, and `artifacts`.

## Deferred V3 Work

These are deliberately outside the MVP:

- Competitor worker and A/B implementation comparison.
- Independent verifier worker.
- Dynamic worker add/stop.
- UI crew timeline and blackboard visualization.
- Automatic patch apply or merge.
- Cross-worker direct messaging.

## Spec Coverage Checklist

- V1/V2 compatibility: covered by Task 9 regression tests.
- Crew records: Task 1 and Task 2.
- Worker roles and lifecycle: Task 1 and Task 4.
- Git worktree allocation and dirty base handling: Task 1 and Task 4.
- Task graph: Task 3.
- Blackboard: Task 2.
- Worker send/observe/attach/tail/status: Task 4, Task 5, CLI in Task 5.
- Verification/challenge/accept: Task 6.
- Changed files: Task 7.
- Merge plan: Task 8.
- UI: deferred by design, not part of V3 MVP.
