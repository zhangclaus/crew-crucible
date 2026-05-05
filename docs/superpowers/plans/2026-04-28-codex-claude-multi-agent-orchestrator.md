# Codex-First Local Multi-Agent Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Python orchestrator where Codex supervises Claude Code tasks in isolated or shared workspaces, records structured runs, and gates risky follow-up actions with deterministic evaluation.

**Architecture:** Build a small Python package around a `Supervisor` that coordinates a `PromptCompiler`, `WorkspaceManager`, `ClaudeCliAdapter`, `RunRecorder`, `PolicyGate`, and `ResultEvaluator`. Persist run state to a local `.orchestrator/` directory and keep the first version subprocess-based so the control flow stays explicit, testable, and easy to evolve into A2A later.

**Tech Stack:** Python 3.11+, Claude Code CLI, stdlib (`argparse`, `dataclasses`, `enum`, `json`, `pathlib`, `subprocess`, `hashlib`, `shutil`, `uuid`), pytest

---

## Scope Check

The approved spec describes one coherent subsystem: a local orchestrator process with one external worker adapter. It does not need to be broken into multiple plans before implementation.

## External Patterns Borrowed

This plan deliberately borrows a small subset of proven open-source agent-management patterns without turning v1 into a full platform:

- [Hermes Agent](https://hermes-agent.nousresearch.com/docs/): keep agent capabilities explicit through profiles/toolsets, expose management commands such as `agents list` and `doctor`, persist run artifacts locally, and keep the design ready for isolated subagents, skills, memory, MCP, and alternate execution backends later.
- [CrewAI hierarchical process](https://docs.crewai.com/en/learn/hierarchical-process) and [Microsoft Agent Framework workflows](https://learn.microsoft.com/en-us/agent-framework/user-guide/workflows/overview): preserve a supervisor/manager role that delegates, validates, and keeps explicit control over task flow instead of allowing uncontrolled peer-to-peer delegation in v1.
- [OpenHands sandbox model](https://docs.openhands.dev/openhands/usage/sandboxes/overview): keep workspace execution behind a sandbox/workspace abstraction so local-copy isolation can later grow into Docker, process, or remote providers without rewriting the supervisor.

Deferred on purpose for v1: persistent user memory, self-created skills, cron scheduling, messaging gateway, MCP client/server support, Docker/SSH/Modal execution backends, and true concurrent scheduling. The first implementation should expose stable seams for these capabilities, not implement them prematurely.

## Execution Preconditions

Before starting Task 1, verify the local runtime:

```bash
command -v python3.11
command -v claude
claude --help | grep -- --json-schema
```

Expected: `python3.11` and `claude` are present, and Claude Code help includes `--json-schema`. If Python 3.11 is unavailable, install or select a Python 3.11 runtime before implementation; do not downgrade the code to Python 3.9 because the plan intentionally uses `StrEnum` and `datetime.UTC`.

After Task 1 creates `.venv`, every `pytest ...` command below may be run as `.venv/bin/python -m pytest ...`. The shorter `pytest ...` form assumes the virtual environment is active.

## File Structure

Create and keep these units focused:

- `pyproject.toml`: package metadata, test configuration, CLI entrypoint
- `.gitignore`: local state and Python cache exclusions
- `src/codex_claude_orchestrator/__init__.py`: package version export
- `src/codex_claude_orchestrator/cli.py`: CLI parser, dependency assembly, JSON output
- `src/codex_claude_orchestrator/models.py`: shared enums and dataclasses
- `src/codex_claude_orchestrator/prompt_compiler.py`: worker task compilation
- `src/codex_claude_orchestrator/agent_registry.py`: agent profiles, toolsets, and adapter construction
- `src/codex_claude_orchestrator/workspace_manager.py`: workspace allocation and change detection
- `src/codex_claude_orchestrator/policy_gate.py`: path and command safety checks
- `src/codex_claude_orchestrator/run_recorder.py`: file-backed task, event, and result persistence
- `src/codex_claude_orchestrator/result_evaluator.py`: deterministic evaluation rules
- `src/codex_claude_orchestrator/adapters/__init__.py`: adapter package marker
- `src/codex_claude_orchestrator/adapters/claude_cli.py`: Claude CLI invocation and structured output parsing
- `src/codex_claude_orchestrator/supervisor.py`: end-to-end orchestration loop
- `tests/test_cli.py`: CLI parser and CLI dispatch integration
- `tests/test_models.py`: dataclass and enum serialization checks
- `tests/test_prompt_compiler.py`: compiled task package checks
- `tests/test_agent_registry.py`: agent profile and toolset checks
- `tests/test_workspace_manager.py`: workspace creation and diff detection checks
- `tests/test_policy_gate.py`: policy allow and deny checks
- `tests/test_run_recorder.py`: persistence checks
- `tests/test_result_evaluator.py`: evaluation classification checks
- `tests/adapters/test_claude_cli.py`: adapter invocation and parsing checks
- `tests/test_supervisor.py`: orchestration flow checks

### Task 1: Project Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/codex_claude_orchestrator/__init__.py`
- Create: `src/codex_claude_orchestrator/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the failing parser test**

```python
# tests/test_cli.py
from codex_claude_orchestrator.cli import build_parser


def test_build_parser_exposes_dispatch_subcommand():
    parser = build_parser()
    subparsers_action = next(action for action in parser._actions if action.dest == "command")
    assert "dispatch" in subparsers_action.choices
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator'`

- [ ] **Step 3: Write the minimal project skeleton**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "codex-claude-orchestrator"
version = "0.1.0"
description = "Local Codex-first orchestrator for Claude Code workers"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.2"]

[project.scripts]
orchestrator = "codex_claude_orchestrator.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

```gitignore
# .gitignore
.venv/
__pycache__/
.pytest_cache/
.orchestrator/
```

```python
# src/codex_claude_orchestrator/__init__.py
__all__ = ["__version__"]

__version__ = "0.1.0"
```

```python
# src/codex_claude_orchestrator/cli.py
import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dispatch = subparsers.add_parser("dispatch", help="Dispatch a task to a worker")
    dispatch.add_argument("--task-id", required=False)
    dispatch.add_argument("--goal", required=True)
    dispatch.add_argument("--repo", required=True)
    dispatch.add_argument(
        "--workspace-mode",
        choices=("isolated", "shared", "readonly"),
        default="isolated",
    )
    dispatch.add_argument(
        "--allow-shared-write",
        action="store_true",
        help="Allow a worker to write directly in shared workspace mode",
    )
    dispatch.add_argument("--assigned-agent", default="claude")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: PASS with `1 passed`

- [ ] **Step 5: Initialize git and commit**

```bash
git init
git add pyproject.toml .gitignore src/codex_claude_orchestrator/__init__.py src/codex_claude_orchestrator/cli.py tests/test_cli.py
git commit -m "chore: scaffold orchestrator project"
```

### Task 2: Shared Models

**Files:**
- Create: `src/codex_claude_orchestrator/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing model serialization test**

```python
# tests/test_models.py
from codex_claude_orchestrator.models import TaskRecord, TaskStatus, WorkspaceMode


def test_task_record_to_dict_normalizes_enum_fields():
    task = TaskRecord(
        task_id="task-1",
        parent_task_id=None,
        origin="user",
        assigned_agent="claude",
        goal="Review the repository",
        task_type="review",
        scope="repo root",
        workspace_mode=WorkspaceMode.ISOLATED,
        status=TaskStatus.QUEUED,
        expected_output_schema={"type": "object"},
    )

    data = task.to_dict()

    assert data["workspace_mode"] == "isolated"
    assert data["status"] == "queued"
    assert data["shared_write_allowed"] is False
    assert data["expected_output_schema"]["type"] == "object"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `codex_claude_orchestrator.models`

- [ ] **Step 3: Implement shared enums and dataclasses**

```python
# src/codex_claude_orchestrator/models.py
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


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


class WorkspaceMode(StrEnum):
    ISOLATED = "isolated"
    SHARED = "shared"
    READONLY = "readonly"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"


class FailureClass(StrEnum):
    INVOCATION_ERROR = "invocation_error"
    EXECUTION_ERROR = "execution_error"
    POLICY_BLOCK = "policy_block"
    QUALITY_REJECT = "quality_reject"
    MERGE_CONFLICT = "merge_conflict"


class NextAction(StrEnum):
    ACCEPT = "accept"
    RETRY_SAME_AGENT = "retry_same_agent"
    RETRY_WITH_TIGHTER_PROMPT = "retry_with_tighter_prompt"
    REROUTE_OTHER_AGENT = "reroute_other_agent"
    ASK_HUMAN = "ask_human"
    DISCARD_WORKSPACE = "discard_workspace"
    PROMOTE_TO_SHARED_MERGE = "promote_to_shared_merge"


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    parent_task_id: str | None
    origin: str
    assigned_agent: str
    goal: str
    task_type: str
    scope: str
    workspace_mode: WorkspaceMode
    status: TaskStatus = TaskStatus.QUEUED
    priority: int = 50
    allowed_tools: list[str] = field(default_factory=list)
    stop_conditions: list[str] = field(default_factory=list)
    verification_expectations: list[str] = field(default_factory=list)
    human_notes: list[str] = field(default_factory=list)
    shared_write_allowed: bool = False
    expected_output_schema: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class RunRecord:
    run_id: str
    task_id: str
    agent: str
    adapter: str
    workspace_id: str
    started_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    status: TaskStatus = TaskStatus.RUNNING
    result_summary: str = ""
    failure_class: FailureClass | None = None
    next_action: NextAction | None = None
    adapter_invocation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class EventRecord:
    event_id: str
    task_id: str
    run_id: str
    from_agent: str
    to_agent: str
    event_type: str
    timestamp: str = field(default_factory=utc_now)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class ArtifactRecord:
    artifact_id: str
    task_id: str
    run_id: str
    kind: str
    path_or_inline_data: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class WorkspaceAllocation:
    workspace_id: str
    path: Path
    mode: WorkspaceMode
    writable: bool
    baseline_snapshot: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class WorkerResult:
    raw_output: str
    stdout: str
    stderr: str
    exit_code: int
    structured_output: dict[str, Any] | None = None
    changed_files: list[str] = field(default_factory=list)
    parse_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


@dataclass(slots=True)
class EvaluationOutcome:
    accepted: bool
    next_action: NextAction
    summary: str
    failure_class: FailureClass | None = None
    needs_human: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)
```

- [ ] **Step 4: Run tests to verify the shared model layer passes**

Run: `pytest tests/test_cli.py tests/test_models.py -v`
Expected: PASS with `2 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/models.py tests/test_models.py
git commit -m "feat: add shared orchestration models"
```

### Task 3: Prompt Compiler

**Files:**
- Create: `src/codex_claude_orchestrator/prompt_compiler.py`
- Create: `tests/test_prompt_compiler.py`

- [ ] **Step 1: Write the failing prompt compiler test**

```python
# tests/test_prompt_compiler.py
from codex_claude_orchestrator.models import TaskRecord, WorkspaceMode
from codex_claude_orchestrator.prompt_compiler import PromptCompiler


def test_compile_returns_metadata_prompt_and_schema():
    task = TaskRecord(
        task_id="task-compiler",
        parent_task_id=None,
        origin="user",
        assigned_agent="claude",
        goal="Implement the prompt compiler",
        task_type="implementation",
        scope="src/codex_claude_orchestrator",
        workspace_mode=WorkspaceMode.ISOLATED,
        allowed_tools=["Read", "Edit", "Bash"],
        stop_conditions=["Stop if tests fail twice"],
        verification_expectations=["Run pytest tests/test_prompt_compiler.py -v"],
        human_notes=["Keep diffs small"],
    )

    compiled = PromptCompiler().compile(task)

    assert compiled.metadata["goal"] == "Implement the prompt compiler"
    assert "Stop if tests fail twice" in compiled.user_prompt
    assert compiled.schema["properties"]["summary"]["type"] == "string"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prompt_compiler.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `prompt_compiler`

- [ ] **Step 3: Implement the prompt compiler**

```python
# src/codex_claude_orchestrator/prompt_compiler.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codex_claude_orchestrator.models import TaskRecord


DEFAULT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "status": {"type": "string", "enum": ["completed", "needs_human", "failed"]},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "verification_commands": {"type": "array", "items": {"type": "string"}},
        "notes_for_supervisor": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "status", "changed_files", "verification_commands", "notes_for_supervisor"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class CompiledPrompt:
    system_prompt: str
    user_prompt: str
    schema: dict[str, Any]
    metadata: dict[str, Any]


class PromptCompiler:
    def compile(self, task: TaskRecord) -> CompiledPrompt:
        schema = task.expected_output_schema or DEFAULT_OUTPUT_SCHEMA
        system_prompt = (
            "You are a bounded worker agent. Stay inside the requested scope, "
            "follow the stop conditions, and return structured output only."
        )
        user_prompt = "\n".join(
            [
                f"Goal: {task.goal}",
                f"Task type: {task.task_type}",
                f"Scope: {task.scope}",
                f"Workspace mode: {task.workspace_mode.value}",
                f"Shared write allowed: {task.shared_write_allowed}",
                f"Allowed tools: {', '.join(task.allowed_tools) or 'none'}",
                f"Stop conditions: {', '.join(task.stop_conditions) or 'none'}",
                f"Verification expectations: {', '.join(task.verification_expectations) or 'none'}",
                f"Human notes: {', '.join(task.human_notes) or 'none'}",
                "If workspace mode is readonly, inspect only and do not modify files.",
                "Return only valid JSON that matches the provided schema.",
            ]
        )
        metadata = {
            "task_id": task.task_id,
            "goal": task.goal,
            "assigned_agent": task.assigned_agent,
            "workspace_mode": task.workspace_mode.value,
            "shared_write_allowed": task.shared_write_allowed,
            "allowed_tools": task.allowed_tools,
            "stop_conditions": task.stop_conditions,
            "verification_expectations": task.verification_expectations,
        }
        return CompiledPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            metadata=metadata,
        )
```

- [ ] **Step 4: Run tests to verify it passes**

Run: `pytest tests/test_prompt_compiler.py tests/test_models.py -v`
Expected: PASS with `2 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/prompt_compiler.py tests/test_prompt_compiler.py
git commit -m "feat: add prompt compiler"
```

### Task 4: Workspace Manager

**Files:**
- Create: `src/codex_claude_orchestrator/workspace_manager.py`
- Create: `tests/test_workspace_manager.py`

- [ ] **Step 1: Write the failing workspace isolation test**

```python
# tests/test_workspace_manager.py
from pathlib import Path

from codex_claude_orchestrator.models import TaskRecord, WorkspaceMode
from codex_claude_orchestrator.workspace_manager import WorkspaceManager


def test_isolated_workspace_copies_repo_and_detects_changes(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("print('one')\n", encoding="utf-8")

    task = TaskRecord(
        task_id="task-workspace",
        parent_task_id=None,
        origin="user",
        assigned_agent="claude",
        goal="Modify app.py",
        task_type="implementation",
        scope="repo root",
        workspace_mode=WorkspaceMode.ISOLATED,
    )

    manager = WorkspaceManager(tmp_path / ".orchestrator")
    allocation = manager.prepare(repo_root, task)

    assert allocation.path != repo_root
    (allocation.path / "app.py").write_text("print('two')\n", encoding="utf-8")
    assert manager.detect_changes(allocation) == ["app.py"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workspace_manager.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `workspace_manager`

- [ ] **Step 3: Implement workspace allocation and change detection**

```python
# src/codex_claude_orchestrator/workspace_manager.py
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from codex_claude_orchestrator.models import TaskRecord, WorkspaceAllocation, WorkspaceMode


class WorkspaceManager:
    def __init__(self, state_root: Path):
        self._state_root = state_root
        self._workspace_root = state_root / "workspaces"
        self._workspace_root.mkdir(parents=True, exist_ok=True)

    def prepare(self, source_repo: Path, task: TaskRecord) -> WorkspaceAllocation:
        source_repo = source_repo.resolve()
        if task.workspace_mode is WorkspaceMode.READONLY:
            return WorkspaceAllocation(
                workspace_id=task.task_id,
                path=source_repo,
                mode=WorkspaceMode.READONLY,
                writable=False,
                baseline_snapshot=self._snapshot_tree(source_repo),
            )
        if task.workspace_mode is WorkspaceMode.SHARED:
            return WorkspaceAllocation(
                workspace_id=task.task_id,
                path=source_repo,
                mode=WorkspaceMode.SHARED,
                writable=True,
                baseline_snapshot=self._snapshot_tree(source_repo),
            )

        workspace_path = self._workspace_root / task.task_id
        if workspace_path.exists():
            shutil.rmtree(workspace_path)
        shutil.copytree(source_repo, workspace_path, ignore=shutil.ignore_patterns(".git", ".orchestrator", "__pycache__", ".pytest_cache"))

        return WorkspaceAllocation(
            workspace_id=task.task_id,
            path=workspace_path,
            mode=WorkspaceMode.ISOLATED,
            writable=True,
            baseline_snapshot=self._snapshot_tree(workspace_path),
        )

    def detect_changes(self, allocation: WorkspaceAllocation) -> list[str]:
        current_snapshot = self._snapshot_tree(allocation.path)
        all_paths = set(allocation.baseline_snapshot) | set(current_snapshot)
        changed = [
            relative_path
            for relative_path in sorted(all_paths)
            if allocation.baseline_snapshot.get(relative_path) != current_snapshot.get(relative_path)
        ]
        return changed

    def _snapshot_tree(self, root: Path) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for file_path in sorted(path for path in root.rglob("*") if path.is_file()):
            relative_path = file_path.relative_to(root).as_posix()
            if relative_path.startswith(".git/") or relative_path.startswith(".orchestrator/"):
                continue
            snapshot[relative_path] = self._hash_file(file_path)
        return snapshot

    def _hash_file(self, file_path: Path) -> str:
        digest = hashlib.sha256()
        digest.update(file_path.read_bytes())
        return digest.hexdigest()
```

- [ ] **Step 4: Run tests to verify it passes**

Run: `pytest tests/test_workspace_manager.py -v`
Expected: PASS with `1 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/workspace_manager.py tests/test_workspace_manager.py
git commit -m "feat: add workspace manager"
```

### Task 5: Policy Gate

**Files:**
- Create: `src/codex_claude_orchestrator/policy_gate.py`
- Create: `tests/test_policy_gate.py`

- [ ] **Step 1: Write the failing policy gate test**

```python
# tests/test_policy_gate.py
from pathlib import Path

from codex_claude_orchestrator.models import WorkspaceAllocation, WorkspaceMode
from codex_claude_orchestrator.policy_gate import PolicyGate


def test_guard_write_targets_blocks_readonly_and_protected_paths(tmp_path: Path):
    gate = PolicyGate(protected_paths=[".env", "secrets/"])

    readonly_allocation = WorkspaceAllocation(
        workspace_id="readonly",
        path=tmp_path,
        mode=WorkspaceMode.READONLY,
        writable=False,
    )
    isolated_allocation = WorkspaceAllocation(
        workspace_id="isolated",
        path=tmp_path,
        mode=WorkspaceMode.ISOLATED,
        writable=True,
    )
    shared_allocation = WorkspaceAllocation(
        workspace_id="shared",
        path=tmp_path,
        mode=WorkspaceMode.SHARED,
        writable=True,
    )

    readonly_decision = gate.guard_write_targets(readonly_allocation, ["app.py"])
    protected_decision = gate.guard_write_targets(isolated_allocation, [".env"])
    shared_preflight = gate.guard_workspace_execution(shared_allocation)
    shared_write = gate.guard_write_targets(shared_allocation, ["app.py"], shared_write_allowed=True)

    assert readonly_decision.allowed is False
    assert "readonly" in readonly_decision.reason
    assert protected_decision.allowed is False
    assert "protected" in protected_decision.reason
    assert shared_preflight.allowed is False
    assert "shared workspace" in shared_preflight.reason
    assert shared_write.allowed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_policy_gate.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `policy_gate`

- [ ] **Step 3: Implement command and path guards**

```python
# src/codex_claude_orchestrator/policy_gate.py
from __future__ import annotations

from codex_claude_orchestrator.models import PolicyDecision, WorkspaceAllocation, WorkspaceMode


class PolicyGate:
    def __init__(self, protected_paths: list[str] | None = None, blocked_command_prefixes: list[tuple[str, ...]] | None = None):
        self._protected_paths = protected_paths or [".env", ".git/", "secrets/"]
        self._blocked_command_prefixes = blocked_command_prefixes or [
            ("rm", "-rf"),
            ("git", "reset", "--hard"),
            ("git", "clean", "-fd"),
        ]

    def guard_workspace_execution(
        self,
        allocation: WorkspaceAllocation,
        *,
        shared_write_allowed: bool = False,
    ) -> PolicyDecision:
        if allocation.mode is WorkspaceMode.SHARED and not shared_write_allowed:
            return PolicyDecision(allowed=False, reason="shared workspace execution requires explicit approval")
        return PolicyDecision(allowed=True, reason=None)

    def guard_write_targets(
        self,
        allocation: WorkspaceAllocation,
        paths: list[str],
        *,
        shared_write_allowed: bool = False,
    ) -> PolicyDecision:
        if allocation.mode is WorkspaceMode.READONLY:
            return PolicyDecision(allowed=False, reason="readonly workspace cannot be modified")
        if allocation.mode is WorkspaceMode.SHARED and not shared_write_allowed:
            return PolicyDecision(allowed=False, reason="shared workspace writes require explicit approval")

        for path in paths:
            normalized = path.lstrip("./")
            if any(
                normalized == protected.rstrip("/") or normalized.startswith(protected)
                for protected in self._protected_paths
            ):
                return PolicyDecision(allowed=False, reason=f"protected path blocked: {path}")

        return PolicyDecision(allowed=True, reason=None)

    def guard_command(self, command: list[str]) -> PolicyDecision:
        for blocked_prefix in self._blocked_command_prefixes:
            if tuple(command[: len(blocked_prefix)]) == blocked_prefix:
                return PolicyDecision(
                    allowed=False,
                    reason=f"blocked command prefix: {' '.join(blocked_prefix)}",
                )
        return PolicyDecision(allowed=True, reason=None)
```

- [ ] **Step 4: Run tests to verify it passes**

Run: `pytest tests/test_policy_gate.py -v`
Expected: PASS with `1 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/policy_gate.py tests/test_policy_gate.py
git commit -m "feat: add policy gate"
```

### Task 6: Run Recorder

**Files:**
- Create: `src/codex_claude_orchestrator/run_recorder.py`
- Create: `tests/test_run_recorder.py`

- [ ] **Step 1: Write the failing run recorder test**

```python
# tests/test_run_recorder.py
from pathlib import Path

from codex_claude_orchestrator.models import (
    EvaluationOutcome,
    EventRecord,
    NextAction,
    RunRecord,
    TaskRecord,
    WorkerResult,
    WorkspaceMode,
)
from codex_claude_orchestrator.prompt_compiler import CompiledPrompt
from codex_claude_orchestrator.run_recorder import RunRecorder


def test_run_recorder_persists_task_run_event_and_evaluation(tmp_path: Path):
    recorder = RunRecorder(tmp_path / ".orchestrator")
    task = TaskRecord(
        task_id="task-record",
        parent_task_id=None,
        origin="user",
        assigned_agent="claude",
        goal="Record a run",
        task_type="review",
        scope="repo root",
        workspace_mode=WorkspaceMode.READONLY,
    )
    run = RunRecord(run_id="run-1", task_id="task-record", agent="claude", adapter="claude-cli", workspace_id="workspace-1")
    compiled = CompiledPrompt(
        system_prompt="system",
        user_prompt="goal",
        schema={"type": "object"},
        metadata={"task_id": task.task_id},
    )
    event = EventRecord(
        event_id="event-1",
        task_id="task-record",
        run_id="run-1",
        from_agent="codex",
        to_agent="claude",
        event_type="task_dispatched",
        payload={"goal": task.goal},
    )
    result = WorkerResult(
        raw_output='{"summary":"done"}',
        stdout='{"summary":"done"}',
        stderr="",
        exit_code=0,
        structured_output={"summary": "done"},
    )
    evaluation = EvaluationOutcome(
        accepted=True,
        next_action=NextAction.ACCEPT,
        summary="worker result accepted",
    )

    recorder.start_run(run, task, compiled)
    recorder.append_event(run.run_id, event)
    recorder.write_result(run.run_id, result, evaluation)

    run_dir = tmp_path / ".orchestrator" / "runs" / "run-1"
    assert (run_dir / "task.json").exists()
    assert (run_dir / "events.jsonl").exists()
    assert (run_dir / "result.json").exists()
    assert (run_dir / "evaluation.json").exists()
    assert (run_dir / "artifacts" / "prompt.txt").exists()
    assert (run_dir / "artifacts" / "stdout.txt").exists()
    assert (run_dir / "artifacts" / "stderr.txt").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `run_recorder`

- [ ] **Step 3: Implement file-backed run recording**

```python
# src/codex_claude_orchestrator/run_recorder.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.models import EvaluationOutcome, EventRecord, RunRecord, TaskRecord, WorkerResult


class RunRecorder:
    def __init__(self, state_root: Path):
        self._state_root = state_root
        self._runs_root = state_root / "runs"
        self._runs_root.mkdir(parents=True, exist_ok=True)

    def start_run(self, run: RunRecord, task: TaskRecord, compiled_prompt: Any | None = None) -> Path:
        run_dir = self._run_dir(run.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(run_dir / "task.json", task.to_dict())
        self._write_json(run_dir / "run.json", run.to_dict())
        if compiled_prompt is not None:
            prompt_text = "\n\n".join(
                [
                    "SYSTEM:",
                    compiled_prompt.system_prompt,
                    "USER:",
                    compiled_prompt.user_prompt,
                ]
            )
            self.write_text_artifact(run.run_id, "prompt.txt", prompt_text)
            self._write_json(run_dir / "artifacts" / "prompt_metadata.json", compiled_prompt.metadata)
            self._write_json(run_dir / "artifacts" / "output_schema.json", compiled_prompt.schema)
        return run_dir

    def append_event(self, run_id: str, event: EventRecord) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        events_path = run_dir / "events.jsonl"
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def write_result(self, run_id: str, result: WorkerResult, evaluation: EvaluationOutcome) -> None:
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(run_dir / "result.json", result.to_dict())
        self._write_json(run_dir / "evaluation.json", evaluation.to_dict())
        self.write_text_artifact(run_id, "stdout.txt", result.stdout)
        self.write_text_artifact(run_id, "stderr.txt", result.stderr)

    def write_text_artifact(self, run_id: str, artifact_name: str, content: str) -> Path:
        artifacts_dir = self._run_dir(run_id) / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifacts_dir / artifact_name
        self._write_text(artifact_path, content)
        return artifact_path

    def _run_dir(self, run_id: str) -> Path:
        return self._runs_root / run_id

    def _write_json(self, path: Path, payload: dict) -> None:
        self._write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
```

- [ ] **Step 4: Run tests to verify it passes**

Run: `pytest tests/test_run_recorder.py -v`
Expected: PASS with `1 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/run_recorder.py tests/test_run_recorder.py
git commit -m "feat: add run recorder"
```

### Task 7: Result Evaluator

**Files:**
- Create: `src/codex_claude_orchestrator/result_evaluator.py`
- Create: `tests/test_result_evaluator.py`

- [ ] **Step 1: Write the failing evaluator test**

```python
# tests/test_result_evaluator.py
from codex_claude_orchestrator.models import FailureClass, NextAction, WorkerResult
from codex_claude_orchestrator.result_evaluator import ResultEvaluator


def test_evaluator_distinguishes_parse_errors_execution_failures_and_success():
    evaluator = ResultEvaluator()

    parse_outcome = evaluator.evaluate(
        WorkerResult(
            raw_output="not-json",
            stdout="not-json",
            stderr="",
            exit_code=0,
            parse_error="invalid json",
        )
    )
    execution_outcome = evaluator.evaluate(
        WorkerResult(
            raw_output="",
            stdout="",
            stderr="command failed",
            exit_code=2,
        )
    )
    success_outcome = evaluator.evaluate(
        WorkerResult(
            raw_output='{"summary":"done","status":"completed"}',
            stdout='{"summary":"done","status":"completed"}',
            stderr="",
            exit_code=0,
            structured_output={
                "summary": "done",
                "status": "completed",
                "changed_files": ["app.py"],
                "verification_commands": ["pytest tests/test_result_evaluator.py -v"],
                "notes_for_supervisor": [],
            },
            changed_files=["app.py"],
        )
    )

    assert parse_outcome.failure_class is FailureClass.INVOCATION_ERROR
    assert parse_outcome.next_action is NextAction.RETRY_WITH_TIGHTER_PROMPT
    assert execution_outcome.failure_class is FailureClass.EXECUTION_ERROR
    assert execution_outcome.next_action is NextAction.RETRY_SAME_AGENT
    assert success_outcome.accepted is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_result_evaluator.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `result_evaluator`

- [ ] **Step 3: Implement deterministic result evaluation**

```python
# src/codex_claude_orchestrator/result_evaluator.py
from __future__ import annotations

from codex_claude_orchestrator.models import EvaluationOutcome, FailureClass, NextAction, PolicyDecision, WorkerResult


class ResultEvaluator:
    def evaluate(
        self,
        result: WorkerResult,
        policy_decision: PolicyDecision | None = None,
    ) -> EvaluationOutcome:
        if policy_decision is not None and not policy_decision.allowed:
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.ASK_HUMAN,
                summary=policy_decision.reason or "policy blocked follow-up action",
                failure_class=FailureClass.POLICY_BLOCK,
                needs_human=True,
            )

        if result.parse_error:
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.RETRY_WITH_TIGHTER_PROMPT,
                summary=f"worker returned unparsable output: {result.parse_error}",
                failure_class=FailureClass.INVOCATION_ERROR,
            )

        if result.exit_code != 0:
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.RETRY_SAME_AGENT,
                summary="worker process exited with a non-zero status",
                failure_class=FailureClass.EXECUTION_ERROR,
            )

        if result.structured_output is None:
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.RETRY_WITH_TIGHTER_PROMPT,
                summary="worker returned no structured payload",
                failure_class=FailureClass.QUALITY_REJECT,
            )

        summary = str(result.structured_output.get("summary", "")).strip()
        status = result.structured_output.get("status")

        if not summary:
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.RETRY_WITH_TIGHTER_PROMPT,
                summary="worker payload is missing a summary",
                failure_class=FailureClass.QUALITY_REJECT,
            )

        if status == "needs_human":
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.ASK_HUMAN,
                summary=summary,
                needs_human=True,
            )

        return EvaluationOutcome(
            accepted=True,
            next_action=NextAction.ACCEPT,
            summary=summary,
        )
```

- [ ] **Step 4: Run tests to verify it passes**

Run: `pytest tests/test_result_evaluator.py -v`
Expected: PASS with `1 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/result_evaluator.py tests/test_result_evaluator.py
git commit -m "feat: add deterministic result evaluator"
```

### Task 8: Claude CLI Adapter

**Files:**
- Create: `src/codex_claude_orchestrator/adapters/__init__.py`
- Create: `src/codex_claude_orchestrator/adapters/claude_cli.py`
- Create: `tests/adapters/test_claude_cli.py`

- [ ] **Step 1: Write the failing Claude adapter test**

```python
# tests/adapters/test_claude_cli.py
from pathlib import Path
from subprocess import CompletedProcess

from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.models import WorkspaceAllocation, WorkspaceMode
from codex_claude_orchestrator.prompt_compiler import CompiledPrompt


def test_execute_uses_json_schema_and_parses_structured_output(tmp_path: Path):
    seen: dict[str, object] = {}

    def fake_runner(command: list[str], **kwargs) -> CompletedProcess[str]:
        seen["command"] = command
        seen["cwd"] = kwargs["cwd"]
        return CompletedProcess(
            args=command,
            returncode=0,
            stdout='{"type":"result","result":{"summary":"done","status":"completed","changed_files":["src/app.py"],"verification_commands":["pytest -q"],"notes_for_supervisor":[]}}',
            stderr="",
        )

    adapter = ClaudeCliAdapter(runner=fake_runner)
    compiled = CompiledPrompt(
        system_prompt="system",
        user_prompt="goal",
        schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
        metadata={"task_id": "task-adapter", "allowed_tools": ["Read", "Edit"]},
    )
    allocation = WorkspaceAllocation(
        workspace_id="workspace-1",
        path=tmp_path,
        mode=WorkspaceMode.ISOLATED,
        writable=True,
    )

    result = adapter.execute(compiled, allocation)

    assert "--json-schema" in seen["command"]
    assert "--output-format" in seen["command"]
    assert "--system-prompt" in seen["command"]
    assert "--allowedTools" in seen["command"]
    assert seen["cwd"] == str(tmp_path)
    assert result.structured_output["summary"] == "done"
    assert result.changed_files == ["src/app.py"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/adapters/test_claude_cli.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `claude_cli`

- [ ] **Step 3: Implement the Claude CLI adapter**

```python
# src/codex_claude_orchestrator/adapters/__init__.py
__all__ = ["claude_cli"]
```

```python
# src/codex_claude_orchestrator/adapters/claude_cli.py
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from subprocess import CompletedProcess

from codex_claude_orchestrator.models import WorkerResult, WorkspaceAllocation
from codex_claude_orchestrator.prompt_compiler import CompiledPrompt


Runner = Callable[..., CompletedProcess[str]]


class ClaudeCliAdapter:
    def __init__(self, runner: Runner | None = None):
        self._runner = runner or subprocess.run

    def build_command(self, compiled: CompiledPrompt) -> list[str]:
        command = [
            "claude",
            "--print",
            compiled.user_prompt,
            "--output-format",
            "json",
            "--system-prompt",
            compiled.system_prompt,
            "--permission-mode",
            "auto",
            "--json-schema",
            json.dumps(compiled.schema, ensure_ascii=False),
        ]
        allowed_tools = compiled.metadata.get("allowed_tools") or []
        if allowed_tools:
            command.extend(["--allowedTools", ",".join(allowed_tools)])
        return command

    def execute(self, compiled: CompiledPrompt, allocation: WorkspaceAllocation) -> WorkerResult:
        command = self.build_command(compiled)
        completed = self._runner(
            command,
            cwd=str(allocation.path),
            text=True,
            capture_output=True,
            check=False,
        )
        stdout = completed.stdout or ""
        try:
            structured_output = self._parse_structured_output(stdout)
            parse_error = None
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            structured_output = None
            parse_error = str(exc)
        changed_files = []
        if isinstance(structured_output, dict):
            changed_files = list(structured_output.get("changed_files") or [])
        return WorkerResult(
            raw_output=stdout,
            stdout=stdout,
            stderr=completed.stderr or "",
            exit_code=completed.returncode,
            structured_output=structured_output,
            changed_files=changed_files,
            parse_error=parse_error,
        )

    def _parse_structured_output(self, stdout: str) -> dict[str, object] | None:
        if not stdout.strip():
            return None
        payload = json.loads(stdout)
        if isinstance(payload, dict) and "result" in payload:
            result = payload["result"]
            if isinstance(result, dict):
                return result
            if isinstance(result, str) and result.strip():
                parsed_result = json.loads(result)
                if isinstance(parsed_result, dict):
                    return parsed_result
            raise ValueError("Claude JSON envelope did not contain an object result")
        if isinstance(payload, dict):
            return payload
        raise ValueError("Claude output was not a JSON object")
```

- [ ] **Step 4: Run tests to verify it passes**

Run: `pytest tests/adapters/test_claude_cli.py -v`
Expected: PASS with `1 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/adapters/__init__.py src/codex_claude_orchestrator/adapters/claude_cli.py tests/adapters/test_claude_cli.py
git commit -m "feat: add claude cli adapter"
```

### Task 9: Supervisor Orchestration

**Files:**
- Create: `src/codex_claude_orchestrator/supervisor.py`
- Create: `tests/test_supervisor.py`

- [ ] **Step 1: Write the failing supervisor flow test**

```python
# tests/test_supervisor.py
from pathlib import Path

from codex_claude_orchestrator.models import TaskRecord, WorkspaceMode, WorkerResult
from codex_claude_orchestrator.policy_gate import PolicyGate
from codex_claude_orchestrator.prompt_compiler import PromptCompiler
from codex_claude_orchestrator.result_evaluator import ResultEvaluator
from codex_claude_orchestrator.run_recorder import RunRecorder
from codex_claude_orchestrator.supervisor import Supervisor
from codex_claude_orchestrator.workspace_manager import WorkspaceManager


class FakeAdapter:
    def build_command(self, compiled):
        return ["claude", "-p", compiled.user_prompt]

    def execute(self, compiled, allocation):
        (allocation.path / "app.py").write_text("print('new')\n", encoding="utf-8")
        return WorkerResult(
            raw_output='{"summary":"done","status":"completed","changed_files":["app.py"],"verification_commands":["pytest -q"],"notes_for_supervisor":[]}',
            stdout='{"summary":"done","status":"completed","changed_files":["app.py"],"verification_commands":["pytest -q"],"notes_for_supervisor":[]}',
            stderr="",
            exit_code=0,
            structured_output={
                "summary": "done",
                "status": "completed",
                "changed_files": ["app.py"],
                "verification_commands": ["pytest -q"],
                "notes_for_supervisor": [],
            },
        )


def test_dispatch_runs_worker_records_run_and_returns_acceptance(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("print('old')\n", encoding="utf-8")
    state_root = tmp_path / ".orchestrator"

    supervisor = Supervisor(
        prompt_compiler=PromptCompiler(),
        workspace_manager=WorkspaceManager(state_root),
        adapter=FakeAdapter(),
        policy_gate=PolicyGate(),
        run_recorder=RunRecorder(state_root),
        result_evaluator=ResultEvaluator(),
    )
    task = TaskRecord(
        task_id="task-supervisor",
        parent_task_id=None,
        origin="user",
        assigned_agent="claude",
        goal="Update app.py",
        task_type="implementation",
        scope="repo root",
        workspace_mode=WorkspaceMode.ISOLATED,
    )

    outcome = supervisor.dispatch(task, repo_root)

    assert outcome.accepted is True
    assert outcome.summary == "done"
    run_root = state_root / "runs"
    assert len(list(run_root.iterdir())) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_supervisor.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `supervisor`

- [ ] **Step 3: Implement the supervisor**

```python
# src/codex_claude_orchestrator/supervisor.py
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.models import EventRecord, RunRecord, TaskRecord, WorkerResult


class Supervisor:
    def __init__(
        self,
        *,
        prompt_compiler,
        workspace_manager,
        adapter,
        policy_gate,
        run_recorder,
        result_evaluator,
    ):
        self._prompt_compiler = prompt_compiler
        self._workspace_manager = workspace_manager
        self._adapter = adapter
        self._policy_gate = policy_gate
        self._run_recorder = run_recorder
        self._result_evaluator = result_evaluator

    def dispatch(self, task: TaskRecord, source_repo: Path):
        compiled = self._prompt_compiler.compile(task)
        allocation = self._workspace_manager.prepare(source_repo, task)
        command = self._adapter.build_command(compiled)
        run = RunRecord(
            run_id=str(uuid4()),
            task_id=task.task_id,
            agent=task.assigned_agent,
            adapter=self._adapter.__class__.__name__,
            workspace_id=allocation.workspace_id,
            adapter_invocation={"command": command},
        )
        self._run_recorder.start_run(run, task, compiled)
        self._run_recorder.append_event(
            run.run_id,
            EventRecord(
                event_id=str(uuid4()),
                task_id=task.task_id,
                run_id=run.run_id,
                from_agent="codex",
                to_agent=task.assigned_agent,
                event_type="task_dispatched",
                payload={"goal": task.goal, "command": command},
            ),
        )

        workspace_decision = self._policy_gate.guard_workspace_execution(
            allocation,
            shared_write_allowed=task.shared_write_allowed,
        )
        if not workspace_decision.allowed:
            result = WorkerResult(
                raw_output="",
                stdout="",
                stderr="",
                exit_code=0,
            )
            evaluation = self._result_evaluator.evaluate(result, workspace_decision)
            self._run_recorder.write_result(run.run_id, result, evaluation)
            return evaluation

        command_decision = self._policy_gate.guard_command(command)
        if not command_decision.allowed:
            result = WorkerResult(
                raw_output="",
                stdout="",
                stderr="",
                exit_code=0,
            )
            evaluation = self._result_evaluator.evaluate(result, command_decision)
            self._run_recorder.write_result(run.run_id, result, evaluation)
            return evaluation

        result = self._adapter.execute(compiled, allocation)
        result.changed_files = self._workspace_manager.detect_changes(allocation)
        write_decision = self._policy_gate.guard_write_targets(
            allocation,
            result.changed_files,
            shared_write_allowed=task.shared_write_allowed,
        )
        evaluation = self._result_evaluator.evaluate(result, write_decision)
        self._run_recorder.write_result(run.run_id, result, evaluation)
        self._run_recorder.append_event(
            run.run_id,
            EventRecord(
                event_id=str(uuid4()),
                task_id=task.task_id,
                run_id=run.run_id,
                from_agent=task.assigned_agent,
                to_agent="codex",
                event_type="evaluation_completed",
                payload=evaluation.to_dict(),
            ),
        )
        return evaluation
```

- [ ] **Step 4: Run tests to verify it passes**

Run: `pytest tests/test_supervisor.py tests/test_workspace_manager.py tests/test_run_recorder.py tests/test_result_evaluator.py -v`
Expected: PASS with `4 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/supervisor.py tests/test_supervisor.py
git commit -m "feat: add supervisor orchestration flow"
```

### Task 10: CLI Dispatch Wiring

**Files:**
- Modify: `src/codex_claude_orchestrator/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI dispatch integration test**

```python
# tests/test_cli.py
import json
from pathlib import Path

from codex_claude_orchestrator.cli import main
from codex_claude_orchestrator.models import EvaluationOutcome, NextAction


def test_build_parser_exposes_dispatch_subcommand():
    from codex_claude_orchestrator.cli import build_parser

    parser = build_parser()
    subparsers_action = next(action for action in parser._actions if action.dest == "command")
    assert "dispatch" in subparsers_action.choices


class FakeSupervisor:
    def dispatch(self, task, source_repo):
        return EvaluationOutcome(
            accepted=True,
            next_action=NextAction.ACCEPT,
            summary=f"accepted {task.goal}",
        )


def test_main_dispatch_prints_json_summary(tmp_path: Path, monkeypatch, capsys):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    monkeypatch.setattr(
        "codex_claude_orchestrator.cli.build_supervisor",
        lambda state_root: FakeSupervisor(),
    )

    exit_code = main(
        [
            "dispatch",
            "--task-id",
            "task-cli",
            "--goal",
            "Inspect the repository",
            "--repo",
            str(repo_root),
            "--workspace-mode",
            "readonly",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["accepted"] is True
    assert payload["summary"] == "accepted Inspect the repository"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL because `main()` does not yet dispatch or print a JSON result

- [ ] **Step 3: Wire the CLI to the orchestrator dependencies**

```python
# src/codex_claude_orchestrator/cli.py
import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.models import TaskRecord, WorkspaceMode
from codex_claude_orchestrator.policy_gate import PolicyGate
from codex_claude_orchestrator.prompt_compiler import PromptCompiler
from codex_claude_orchestrator.result_evaluator import ResultEvaluator
from codex_claude_orchestrator.run_recorder import RunRecorder
from codex_claude_orchestrator.supervisor import Supervisor
from codex_claude_orchestrator.workspace_manager import WorkspaceManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dispatch = subparsers.add_parser("dispatch", help="Dispatch a task to a worker")
    dispatch.add_argument("--task-id", required=False)
    dispatch.add_argument("--goal", required=True)
    dispatch.add_argument("--repo", required=True)
    dispatch.add_argument(
        "--workspace-mode",
        choices=("isolated", "shared", "readonly"),
        default="isolated",
    )
    dispatch.add_argument(
        "--allow-shared-write",
        action="store_true",
        help="Allow a worker to write directly in shared workspace mode",
    )
    dispatch.add_argument("--assigned-agent", default="claude")
    return parser


def build_supervisor(state_root: Path) -> Supervisor:
    return Supervisor(
        prompt_compiler=PromptCompiler(),
        workspace_manager=WorkspaceManager(state_root),
        adapter=ClaudeCliAdapter(),
        policy_gate=PolicyGate(),
        run_recorder=RunRecorder(state_root),
        result_evaluator=ResultEvaluator(),
    )


def default_allowed_tools(workspace_mode: WorkspaceMode, shared_write_allowed: bool) -> list[str]:
    read_tools = ["Read", "Glob", "Grep", "LS"]
    write_tools = ["Edit", "MultiEdit", "Write", "Bash"]
    if workspace_mode is WorkspaceMode.READONLY:
        return read_tools
    if workspace_mode is WorkspaceMode.SHARED and not shared_write_allowed:
        return read_tools
    return read_tools + write_tools


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "dispatch":
        raise ValueError(f"Unsupported command: {args.command}")

    repo_root = Path(args.repo).resolve()
    supervisor = build_supervisor(repo_root / ".orchestrator")
    workspace_mode = WorkspaceMode(args.workspace_mode)
    task = TaskRecord(
        task_id=args.task_id or f"task-{uuid4()}",
        parent_task_id=None,
        origin="cli",
        assigned_agent=args.assigned_agent,
        goal=args.goal,
        task_type="adhoc",
        scope=str(repo_root),
        workspace_mode=workspace_mode,
        allowed_tools=default_allowed_tools(workspace_mode, args.allow_shared_write),
        shared_write_allowed=args.allow_shared_write,
    )
    outcome = supervisor.dispatch(task, repo_root)
    print(json.dumps(outcome.to_dict(), ensure_ascii=False))
    return 0
```

- [ ] **Step 4: Run tests to verify it passes**

Run: `pytest tests/test_cli.py tests/test_supervisor.py tests/adapters/test_claude_cli.py -v`
Expected: PASS with `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/cli.py tests/test_cli.py
git commit -m "feat: wire cli dispatch to supervisor"
```

### Task 11: Agent Registry and Management CLI

**Files:**
- Create: `src/codex_claude_orchestrator/agent_registry.py`
- Create: `tests/test_agent_registry.py`
- Modify: `src/codex_claude_orchestrator/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing agent registry test**

```python
# tests/test_agent_registry.py
from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.agent_registry import AgentRegistry
from codex_claude_orchestrator.models import WorkspaceMode


def test_default_registry_exposes_claude_profile_and_toolsets():
    registry = AgentRegistry.default()

    profile = registry.get("claude")
    readonly_tools = registry.allowed_tools(
        "claude",
        WorkspaceMode.READONLY,
        shared_write_allowed=False,
    )
    isolated_tools = registry.allowed_tools(
        "claude",
        WorkspaceMode.ISOLATED,
        shared_write_allowed=False,
    )
    shared_without_approval = registry.allowed_tools(
        "claude",
        WorkspaceMode.SHARED,
        shared_write_allowed=False,
    )

    assert profile.name == "claude"
    assert profile.adapter == "claude-cli"
    assert profile.default_workspace_mode is WorkspaceMode.ISOLATED
    assert "Read" in readonly_tools
    assert "Edit" not in readonly_tools
    assert "Edit" in isolated_tools
    assert "Edit" not in shared_without_approval


def test_default_registry_builds_claude_adapter():
    registry = AgentRegistry.default()

    adapter = registry.build_adapter("claude")

    assert isinstance(adapter, ClaudeCliAdapter)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_registry.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `agent_registry`

- [ ] **Step 3: Implement the agent registry**

```python
# src/codex_claude_orchestrator/agent_registry.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.models import WorkspaceMode


@dataclass(frozen=True, slots=True)
class AgentProfile:
    name: str
    adapter: str
    description: str
    default_workspace_mode: WorkspaceMode
    readonly_tools: tuple[str, ...]
    write_tools: tuple[str, ...]
    supports_shared_workspace: bool = False
    max_concurrent_runs: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "adapter": self.adapter,
            "description": self.description,
            "default_workspace_mode": self.default_workspace_mode.value,
            "readonly_tools": list(self.readonly_tools),
            "write_tools": list(self.write_tools),
            "supports_shared_workspace": self.supports_shared_workspace,
            "max_concurrent_runs": self.max_concurrent_runs,
        }


class AgentRegistry:
    def __init__(self, profiles: list[AgentProfile]):
        self._profiles = {profile.name: profile for profile in profiles}

    @classmethod
    def default(cls) -> AgentRegistry:
        return cls(
            [
                AgentProfile(
                    name="claude",
                    adapter="claude-cli",
                    description="Claude Code worker invoked through the local CLI",
                    default_workspace_mode=WorkspaceMode.ISOLATED,
                    readonly_tools=("Read", "Glob", "Grep", "LS"),
                    write_tools=("Edit", "MultiEdit", "Write", "Bash"),
                    supports_shared_workspace=True,
                    max_concurrent_runs=1,
                )
            ]
        )

    def names(self) -> list[str]:
        return sorted(self._profiles)

    def list_profiles(self) -> list[AgentProfile]:
        return [self._profiles[name] for name in self.names()]

    def get(self, name: str) -> AgentProfile:
        try:
            return self._profiles[name]
        except KeyError as exc:
            known_agents = ", ".join(self.names()) or "none"
            raise KeyError(f"unknown agent {name!r}; known agents: {known_agents}") from exc

    def allowed_tools(
        self,
        name: str,
        workspace_mode: WorkspaceMode,
        *,
        shared_write_allowed: bool = False,
    ) -> list[str]:
        profile = self.get(name)
        tools = list(profile.readonly_tools)
        if workspace_mode is WorkspaceMode.READONLY:
            return tools
        if workspace_mode is WorkspaceMode.SHARED and not shared_write_allowed:
            return tools
        return tools + list(profile.write_tools)

    def build_adapter(self, name: str):
        profile = self.get(name)
        if profile.adapter == "claude-cli":
            return ClaudeCliAdapter()
        raise ValueError(f"unsupported adapter: {profile.adapter}")
```

- [ ] **Step 4: Run tests to verify it passes**

Run: `pytest tests/test_agent_registry.py tests/adapters/test_claude_cli.py -v`
Expected: PASS with `3 passed`

- [ ] **Step 5: Write the failing management CLI tests**

Append these tests to `tests/test_cli.py`:

```python
def test_agents_list_prints_configured_profiles(capsys):
    exit_code = main(["agents", "list"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["agents"][0]["name"] == "claude"
    assert payload["agents"][0]["adapter"] == "claude-cli"


def test_doctor_reports_python_and_claude_checks(capsys):
    exit_code = main(["doctor"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["python"]["ok"] is True
    assert "claude_cli" in payload
```

- [ ] **Step 6: Run CLI tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL because `agents` and `doctor` commands do not exist yet

- [ ] **Step 7: Wire the registry into the CLI**

Replace `src/codex_claude_orchestrator/cli.py` with:

```python
# src/codex_claude_orchestrator/cli.py
import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.agent_registry import AgentRegistry
from codex_claude_orchestrator.models import TaskRecord, WorkspaceMode
from codex_claude_orchestrator.policy_gate import PolicyGate
from codex_claude_orchestrator.prompt_compiler import PromptCompiler
from codex_claude_orchestrator.result_evaluator import ResultEvaluator
from codex_claude_orchestrator.run_recorder import RunRecorder
from codex_claude_orchestrator.supervisor import Supervisor
from codex_claude_orchestrator.workspace_manager import WorkspaceManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dispatch = subparsers.add_parser("dispatch", help="Dispatch a task to a worker")
    dispatch.add_argument("--task-id", required=False)
    dispatch.add_argument("--goal", required=True)
    dispatch.add_argument("--repo", required=True)
    dispatch.add_argument(
        "--workspace-mode",
        choices=("isolated", "shared", "readonly"),
        default="isolated",
    )
    dispatch.add_argument(
        "--allow-shared-write",
        action="store_true",
        help="Allow a worker to write directly in shared workspace mode",
    )
    dispatch.add_argument("--assigned-agent", default="claude")

    agents = subparsers.add_parser("agents", help="Manage configured worker agents")
    agent_subparsers = agents.add_subparsers(dest="agent_command", required=True)
    agent_subparsers.add_parser("list", help="List configured worker agents")

    subparsers.add_parser("doctor", help="Check local orchestrator prerequisites")
    return parser


def build_supervisor(state_root: Path) -> Supervisor:
    return Supervisor(
        prompt_compiler=PromptCompiler(),
        workspace_manager=WorkspaceManager(state_root),
        adapter=ClaudeCliAdapter(),
        policy_gate=PolicyGate(),
        run_recorder=RunRecorder(state_root),
        result_evaluator=ResultEvaluator(),
    )


def run_doctor(registry: AgentRegistry) -> dict[str, object]:
    python_ok = sys.version_info >= (3, 11)
    claude_path = shutil.which("claude")
    return {
        "python": {
            "ok": python_ok,
            "version": sys.version.split()[0],
            "required": ">=3.11",
        },
        "claude_cli": {
            "ok": claude_path is not None,
            "path": claude_path,
        },
        "agents": [profile.to_dict() for profile in registry.list_profiles()],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    registry = AgentRegistry.default()

    if args.command == "agents":
        if args.agent_command == "list":
            print(json.dumps({"agents": [profile.to_dict() for profile in registry.list_profiles()]}, ensure_ascii=False))
            return 0
        raise ValueError(f"Unsupported agents command: {args.agent_command}")

    if args.command == "doctor":
        print(json.dumps(run_doctor(registry), ensure_ascii=False))
        return 0

    if args.command != "dispatch":
        raise ValueError(f"Unsupported command: {args.command}")

    repo_root = Path(args.repo).resolve()
    workspace_mode = WorkspaceMode(args.workspace_mode)
    profile = registry.get(args.assigned_agent)
    supervisor = build_supervisor(repo_root / ".orchestrator")
    task = TaskRecord(
        task_id=args.task_id or f"task-{uuid4()}",
        parent_task_id=None,
        origin="cli",
        assigned_agent=profile.name,
        goal=args.goal,
        task_type="adhoc",
        scope=str(repo_root),
        workspace_mode=workspace_mode,
        allowed_tools=registry.allowed_tools(
            profile.name,
            workspace_mode,
            shared_write_allowed=args.allow_shared_write,
        ),
        shared_write_allowed=args.allow_shared_write,
    )
    outcome = supervisor.dispatch(task, repo_root)
    print(json.dumps(outcome.to_dict(), ensure_ascii=False))
    return 0
```

- [ ] **Step 8: Run management CLI tests to verify they pass**

Run: `pytest tests/test_cli.py tests/test_agent_registry.py -v`
Expected: PASS with `6 passed`

- [ ] **Step 9: Commit**

```bash
git add src/codex_claude_orchestrator/agent_registry.py src/codex_claude_orchestrator/cli.py tests/test_agent_registry.py tests/test_cli.py
git commit -m "feat: add agent registry and management cli"
```

### Task 12: Final Verification and Claude Smoke Check

**Files:**
- None

- [ ] **Step 1: Run the full unit suite**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS with all collected tests passing.

- [ ] **Step 2: Verify the installed CLI entrypoint**

Run: `.venv/bin/orchestrator --help`
Expected: exit code 0 and help text that lists the `dispatch` command.

- [ ] **Step 3: Create a small smoke-test repository**

Run:

```bash
python3.11 -c "from pathlib import Path; root = Path('/private/tmp/codex-claude-orchestrator-smoke'); root.mkdir(parents=True, exist_ok=True); (root / 'app.py').write_text(\"print('hello from smoke')\\n\", encoding='utf-8')"
```

Expected: `/private/tmp/codex-claude-orchestrator-smoke/app.py` exists.

- [ ] **Step 4: Run one readonly Claude dispatch**

Run:

```bash
.venv/bin/orchestrator dispatch --goal "Inspect app.py and summarize what it prints. Do not edit files." --repo /private/tmp/codex-claude-orchestrator-smoke --workspace-mode readonly
```

Expected: exit code 0 and stdout is JSON with `accepted`, `next_action`, and `summary` keys. If this fails because Claude Code is not authenticated, stop and run Claude Code authentication before continuing.

- [ ] **Step 5: Confirm run artifacts were recorded**

Run: `find /private/tmp/codex-claude-orchestrator-smoke/.orchestrator/runs -maxdepth 3 -type f`
Expected: output includes `task.json`, `run.json`, `result.json`, `evaluation.json`, `artifacts/prompt.txt`, `artifacts/stdout.txt`, and `artifacts/stderr.txt`.

## Spec Coverage Check

- Supervisor architecture: covered by Tasks 2, 7, 8, 9, and 10
- PromptCompiler: covered by Task 3
- WorkspaceManager and isolation modes: covered by Task 4
- PolicyGate: covered by Task 5
- RunRecorder and artifact persistence: covered by Task 6
- ResultEvaluator and failure taxonomy: covered by Task 7
- Claude adapter: covered by Task 8
- Agent profile, toolset, and management commands: covered by Task 11
- CLI entrypoint for local personal use: covered by Tasks 1, 10, 11, and 12
- Mesh-ready task, run, event, and artifact model: covered by Task 2
- Real Claude Code invocation smoke coverage: covered by Task 12

## Placeholder Scan

Run:

```bash
python - <<'PY'
from pathlib import Path

text = Path("docs/superpowers/plans/2026-04-28-codex-claude-multi-agent-orchestrator.md").read_text()
checks = [
    "T" + "BD",
    "TO" + "DO",
    "FIX" + "ME",
    "implement " + "later",
    "similar " + "to Task",
    "appropriate " + "error handling",
    "edge " + "cases",
]
offenders = {check: text.count(check) for check in checks if text.count(check)}
print(offenders)
assert not offenders, offenders
PY
```

Expected: prints `{}` and exits successfully

## Type Consistency Check

- `WorkspaceMode`, `TaskStatus`, `FailureClass`, and `NextAction` are defined once in `models.py` and reused consistently.
- `TaskRecord.shared_write_allowed` is defined once in `models.py`, populated by the CLI, and consumed by `Supervisor` and `PolicyGate`.
- `RunRecord.adapter_invocation` is defined once in `models.py` and populated by `Supervisor` before `RunRecorder.start_run()`.
- `AgentProfile` lives in `agent_registry.py`; it depends on `WorkspaceMode` and stays independent from task/run persistence models.
- `AgentRegistry.allowed_tools()` is the single source for CLI default toolsets; `PromptCompiler` only consumes the final task-level `allowed_tools`.
- `TaskRecord`, `RunRecord`, `EventRecord`, `WorkerResult`, `PolicyDecision`, and `EvaluationOutcome` are defined before any task that uses them.
- `CompiledPrompt` is introduced in the prompt compiler task before the Claude adapter task depends on it.
- `Supervisor.dispatch()` returns `EvaluationOutcome`, which matches the CLI JSON output and the supervisor test expectations.
