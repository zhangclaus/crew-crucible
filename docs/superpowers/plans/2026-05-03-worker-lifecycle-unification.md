# Worker Lifecycle Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify worker lifecycle management by introducing a claim/release protocol, a single write-scope module, consolidated state, and IDLE-based worker reuse.

**Architecture:** Four phases: (1) unify write-scope checking into `crew/scope.py`, (2) add BUSY/IDLE states with claim/release on `WorkerPool`, (3) consolidate `active_worker_ids` derivation in `CrewRecorder`, (4) integrate claim/release into V4 supervisor turn lifecycle.

**Tech Stack:** Python 3.12+, pytest, JSONL-based persistence via `CrewRecorder`

**Spec:** `docs/superpowers/specs/2026-05-03-worker-lifecycle-unification-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `src/codex_claude_orchestrator/crew/scope.py` | Unified write-scope normalization and matching |
| Create | `tests/crew/test_scope.py` | Tests for scope module |
| Modify | `src/codex_claude_orchestrator/crew/models.py:41-46` | Add `BUSY` to `WorkerStatus` |
| Modify | `src/codex_claude_orchestrator/workers/pool.py` | Add claim/release, replace scope functions, use derived active_worker_ids |
| Modify | `src/codex_claude_orchestrator/state/crew_recorder.py` | Add `transition_worker_status`, `active_worker_ids()` |
| Modify | `src/codex_claude_orchestrator/v4/planner.py:47-73` | Replace `_is_active` and `_scope_covers` |
| Modify | `src/codex_claude_orchestrator/crew/decision_policy.py:208-222` | Use `is_terminal_worker_status`, exclude BUSY |
| Modify | `src/codex_claude_orchestrator/crew/gates.py:118-134` | Replace `_is_in_scope` and `_is_protected` with scope module |
| Modify | `src/codex_claude_orchestrator/v4/crew_runner.py:578-605` | Replace `_scope_covers`, remove bidirectional prefix bug |
| Modify | `src/codex_claude_orchestrator/crew/controller.py:142-175` | Remove duplicate active_worker_ids management, add claim/release pass-through |
| Modify | `tests/workers/test_pool.py` | Add claim/release tests |
| Modify | `tests/v4/test_planner.py` | Add IDLE status tests |
| Modify | `tests/v4/test_crew_runner.py` | Update scope tests to use unified module |

---

## Task 1: Unified Write-Scope Module

### 1.1 Create scope.py with tests (TDD)

**Files:**
- Create: `src/codex_claude_orchestrator/crew/scope.py`
- Create: `tests/crew/test_scope.py`

- [ ] **Step 1: Write failing tests for scope module**

```python
# tests/crew/test_scope.py
from codex_claude_orchestrator.crew.scope import normalize_path, scope_covers, scope_covers_all, is_protected


class TestNormalizePath:
    def test_strips_leading_dot_slash(self):
        assert normalize_path("./src/main.py") == "src/main.py"

    def test_strips_leading_slash(self):
        assert normalize_path("/src/main.py") == "src/main.py"

    def test_normalizes_backslashes(self):
        assert normalize_path("src\\main.py") == "src/main.py"

    def test_strips_whitespace(self):
        assert normalize_path("  src/main.py  ") == "src/main.py"

    def test_empty_string(self):
        assert normalize_path("") == ""

    def test_chained_dot_slash(self):
        assert normalize_path("././src/main.py") == "src/main.py"


class TestScopeCovers:
    def test_empty_scope_returns_false(self):
        assert scope_covers([], "src/main.py") is False

    def test_empty_target_returns_true(self):
        assert scope_covers(["src/"], "") is True

    def test_exact_match_file(self):
        assert scope_covers(["src/main.py"], "src/main.py") is True

    def test_exact_match_directory(self):
        assert scope_covers(["src/"], "src/") is True

    def test_subdirectory_match(self):
        assert scope_covers(["src/"], "src/app/main.py") is True

    def test_nested_subdirectory_match(self):
        assert scope_covers(["src/"], "src/app/deep/file.py") is True

    def test_no_match_sibling_directory(self):
        assert scope_covers(["src/"], "tests/test_main.py") is False

    def test_no_match_parent_directory(self):
        assert scope_covers(["src/app/"], "src/main.py") is False

    def test_no_match_prefix_only(self):
        """src/ should NOT match srca/"""
        assert scope_covers(["src/"], "srca/main.py") is False

    def test_multiple_scopes(self):
        assert scope_covers(["src/", "tests/"], "tests/test_main.py") is True
        assert scope_covers(["src/", "tests/"], "docs/readme.md") is False

    def test_scope_without_trailing_slash(self):
        assert scope_covers(["src"], "src/main.py") is True

    def test_target_with_backslash(self):
        assert scope_covers(["src/"], "src\\main.py") is True

    def test_scope_with_dot_slash(self):
        assert scope_covers(["./src/"], "src/main.py") is True

    def test_bidirectional_prefix_bug_fixed(self):
        """src/app/ should NOT cover src/ (bidirectional prefix was the old bug)"""
        assert scope_covers(["src/app/"], "src/main.py") is False

    def test_file_scope_exact(self):
        assert scope_covers(["pyproject.toml"], "pyproject.toml") is True

    def test_file_scope_no_subdirectory(self):
        assert scope_covers(["pyproject.toml"], "pyproject.toml.bak") is False


class TestScopeCoversAll:
    def test_all_covered(self):
        assert scope_covers_all(["src/", "tests/"], ["src/main.py", "tests/test.py"]) is True

    def test_one_not_covered(self):
        assert scope_covers_all(["src/"], ["src/main.py", "docs/readme.md"]) is False

    def test_empty_targets(self):
        assert scope_covers_all(["src/"], []) is True

    def test_empty_scope_with_targets(self):
        assert scope_covers_all([], ["src/main.py"]) is False


class TestIsProtected:
    def test_git_directory(self):
        assert is_protected(".git/config", [".git/"]) is True

    def test_env_file(self):
        assert is_protected(".env", [".env"]) is True

    def test_pem_file(self):
        assert is_protected("certs/server.pem", ["*.pem"]) is True

    def test_not_protected(self):
        assert is_protected("src/main.py", [".git/", ".env", "*.pem"]) is False

    def test_workflows_directory(self):
        assert is_protected(".github/workflows/ci.yml", [".github/workflows/"]) is True

    def test_protected_with_backslash(self):
        assert is_protected(".git\\config", [".git/"]) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/crew/test_scope.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.crew.scope'`

- [ ] **Step 3: Implement scope.py**

```python
# src/codex_claude_orchestrator/crew/scope.py
"""Unified write-scope normalization and matching.

All write-scope checks in the codebase MUST use these functions.
Do NOT implement scope matching elsewhere.
"""

from __future__ import annotations

from fnmatch import fnmatch


def normalize_path(path: str) -> str:
    """Normalize a path for scope comparison.

    - Forward slashes only
    - Strip leading ./ and /
    - Strip whitespace
    """
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    while normalized.startswith("/"):
        normalized = normalized[1:]
    return normalized


def scope_covers(write_scope: list[str], target_path: str) -> bool:
    """Check if write_scope covers target_path.

    A scope covers a target if:
    - target == scope (exact match)
    - target is a subdirectory of scope (prefix match with /)

    Returns False if write_scope is empty.
    Returns True if target_path is empty (nothing to constrain).
    """
    target = normalize_path(target_path)
    if not target:
        return True
    for scope in write_scope:
        s = normalize_path(scope)
        if not s:
            continue
        # Directory prefix matching
        if not s.endswith("/"):
            s += "/"
        target_with_slash = target if target.endswith("/") else target + "/"
        if target_with_slash.startswith(s) or target == normalize_path(scope):
            return True
    return False


def scope_covers_all(write_scope: list[str], target_paths: list[str]) -> bool:
    """Check if write_scope covers ALL target paths."""
    return all(scope_covers(write_scope, p) for p in target_paths if p)


def is_protected(path: str, protected_patterns: list[str]) -> bool:
    """Check if a path matches any protected pattern."""
    normalized = normalize_path(path)
    for pattern in protected_patterns:
        p = normalize_path(pattern)
        if p.endswith("/"):
            if normalized.startswith(p):
                return True
        elif fnmatch(normalized, p):
            return True
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/crew/test_scope.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/crew/scope.py tests/crew/test_scope.py
git commit -m "feat: add unified write-scope module (crew/scope.py)"
```

### 1.2 Migrate pool.py to use scope module

**Files:**
- Modify: `src/codex_claude_orchestrator/workers/pool.py:533-558`
- Modify: `tests/workers/test_pool.py`

- [ ] **Step 1: Update pool.py imports and replace scope functions**

In `pool.py`, add at the top (after existing imports):
```python
from codex_claude_orchestrator.crew.scope import scope_covers as _unified_scope_covers
```

Replace the `find_compatible_worker` method's write_scope check (line 204):
```python
# OLD (line 204):
if not _write_scope_covers(worker.get("write_scope", []), contract.write_scope):
    continue

# NEW:
if not _unified_scope_covers(worker.get("write_scope", []), contract.write_scope[0] if contract.write_scope else ""):
    continue
```

Wait -- the `scope_covers` function takes `(write_scope, target_path)` where `target_path` is a single path, but `contract.write_scope` is a `list[str]`. The caller needs to check that the worker's scope covers ALL requested paths. Use `scope_covers_all` instead.

Replace line 204:
```python
# OLD:
if not _write_scope_covers(worker.get("write_scope", []), contract.write_scope):
    continue

# NEW:
from codex_claude_orchestrator.crew.scope import scope_covers_all
if not scope_covers_all(worker.get("write_scope", []), contract.write_scope):
    continue
```

Update the import at the top of the file to:
```python
from codex_claude_orchestrator.crew.scope import scope_covers_all as _scope_covers_all
```

And line 204 becomes:
```python
if not _scope_covers_all(worker.get("write_scope", []), contract.write_scope):
    continue
```

Remove the module-level `_write_scope_covers`, `_scope_covers`, `_normalize_scope`, and `_string_items` functions (lines 533-558).

- [ ] **Step 2: Run existing pool tests**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/workers/test_pool.py -v`
Expected: ALL PASS (existing tests should still pass with unified scope)

- [ ] **Step 3: Commit**

```bash
git add src/codex_claude_orchestrator/workers/pool.py
git commit -m "refactor: migrate pool.py to unified scope module"
```

### 1.3 Migrate planner.py to use scope module

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/planner.py:55-77`
- Modify: `tests/v4/test_planner.py`

- [ ] **Step 1: Replace planner scope functions**

In `planner.py`, add import:
```python
from codex_claude_orchestrator.crew.scope import scope_covers_all as _scope_covers_all
```

Replace `_write_scope_covers` call in `select_worker` (line 34):
```python
# OLD:
and _write_scope_covers(worker.get("write_scope", []), requested_write_scope)

# NEW:
and _scope_covers_all(worker.get("write_scope", []), requested_write_scope)
```

Remove the module-level `_write_scope_covers`, `_scope_covers`, `_normalize_scope`, `_string_items` functions (lines 55-83). Keep `_is_active`, `_authority_covers`.

- [ ] **Step 2: Run planner tests**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/v4/test_planner.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/codex_claude_orchestrator/v4/planner.py
git commit -m "refactor: migrate planner.py to unified scope module"
```

### 1.4 Migrate gates.py to use scope module

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/gates.py:56-134`

- [ ] **Step 1: Replace gates.py scope functions**

In `gates.py`, add import:
```python
from codex_claude_orchestrator.crew.scope import scope_covers, is_protected as _is_protected_path
```

Replace `_is_in_scope` method in `WriteScopeGate` (lines 118-125):
```python
# OLD:
def _is_in_scope(self, path: str, write_scope: list[str]) -> bool:
    for scope in write_scope:
        if scope.endswith("/"):
            if path.startswith(scope):
                return True
        elif path == scope or path.startswith(f"{scope}/"):
            return True
    return False

# NEW:
def _is_in_scope(self, path: str, write_scope: list[str]) -> bool:
    return scope_covers(write_scope, path)
```

Replace `_is_protected` method (lines 127-134):
```python
# OLD:
def _is_protected(self, path: str) -> bool:
    for pattern in self.protected_patterns:
        if pattern.endswith("/"):
            if path.startswith(pattern):
                return True
        elif fnmatch(path, pattern):
            return True
    return False

# NEW:
def _is_protected(self, path: str) -> bool:
    return _is_protected_path(path, self.protected_patterns)
```

Remove the `fnmatch` import from gates.py (line 5) since it's no longer used directly.

- [ ] **Step 2: Run gates tests**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/crew/test_gates.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/codex_claude_orchestrator/crew/gates.py
git commit -m "refactor: migrate gates.py to unified scope module"
```

### 1.5 Migrate crew_runner.py to use scope module (fix bidirectional bug)

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/crew_runner.py:578-605`
- Modify: `tests/v4/test_crew_runner.py`

- [ ] **Step 1: Replace crew_runner.py scope functions**

In `crew_runner.py`, add import:
```python
from codex_claude_orchestrator.crew.scope import scope_covers_all as _scope_covers_all
```

Replace `_is_incompatible_source_worker` method (lines 578-592) to use unified scope:
```python
def _is_incompatible_source_worker(
    self, worker: dict[str, Any], requested_write_scope: list[str]
) -> bool:
    if worker.get("role") != WorkerRole.IMPLEMENTER.value:
        return False
    if is_terminal_worker_status(worker.get("status", "running")):
        return False
    worker_scope = worker.get("write_scope") or []
    if not worker_scope:
        return False
    if not requested_write_scope:
        return False
    return not _scope_covers_all(worker_scope, requested_write_scope)
```

Remove the `_scope_covers` static method (lines 594-605) entirely.

- [ ] **Step 2: Run crew_runner tests**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/v4/test_crew_runner.py -v`
Expected: ALL PASS (the bidirectional prefix bug fix means some edge cases may now correctly reject previously-accepted workers)

- [ ] **Step 3: Commit**

```bash
git add src/codex_claude_orchestrator/v4/crew_runner.py
git commit -m "refactor: migrate crew_runner.py to unified scope module, fix bidirectional prefix bug"
```

### 1.6 Full regression test

- [ ] **Step 1: Run entire test suite**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest --tb=short`
Expected: ALL 588+ tests PASS

---

## Task 2: Claim/Release Protocol

### 2.1 Add BUSY status to WorkerStatus

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/models.py:41-46`

- [ ] **Step 1: Add BUSY to WorkerStatus enum**

```python
# models.py lines 41-46, change to:
class WorkerStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    BUSY = "busy"
    IDLE = "idle"
    FAILED = "failed"
    STOPPED = "stopped"
```

- [ ] **Step 2: Run models tests**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/crew/test_models.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/codex_claude_orchestrator/crew/models.py
git commit -m "feat: add BUSY status to WorkerStatus enum"
```

### 2.2 Add claim/release methods to WorkerPool

**Files:**
- Modify: `src/codex_claude_orchestrator/workers/pool.py`
- Modify: `tests/workers/test_pool.py`

- [ ] **Step 1: Write failing tests for claim/release**

Add to `tests/workers/test_pool.py`:

```python
def test_claim_worker_transitions_to_busy(tmp_path):
    crew_id = "crew-1"
    recorder = CrewRecorder(tmp_path)
    recorder.create_crew(crew_id, repo=tmp_path, root_goal="test")
    pool = _make_pool(recorder=recorder, repo_root=tmp_path)

    worker = pool.ensure_worker(
        repo_root=tmp_path,
        crew=_make_crew(crew_id),
        contract=_make_contract(),
    )
    assert worker.status == WorkerStatus.RUNNING

    pool.claim_worker(crew_id, worker.worker_id)

    details = recorder.read_crew(crew_id)
    claimed = [w for w in details["workers"] if w["worker_id"] == worker.worker_id][0]
    assert claimed["status"] == "busy"


def test_release_worker_transitions_to_idle(tmp_path):
    crew_id = "crew-1"
    recorder = CrewRecorder(tmp_path)
    recorder.create_crew(crew_id, repo=tmp_path, root_goal="test")
    pool = _make_pool(recorder=recorder, repo_root=tmp_path)

    worker = pool.ensure_worker(
        repo_root=tmp_path,
        crew=_make_crew(crew_id),
        contract=_make_contract(),
    )
    pool.claim_worker(crew_id, worker.worker_id)
    pool.release_worker(crew_id, worker.worker_id)

    details = recorder.read_crew(crew_id)
    released = [w for w in details["workers"] if w["worker_id"] == worker.worker_id][0]
    assert released["status"] == "idle"


def test_claim_worker_rejects_busy_worker(tmp_path):
    crew_id = "crew-1"
    recorder = CrewRecorder(tmp_path)
    recorder.create_crew(crew_id, repo=tmp_path, root_goal="test")
    pool = _make_pool(recorder=recorder, repo_root=tmp_path)

    worker = pool.ensure_worker(
        repo_root=tmp_path,
        crew=_make_crew(crew_id),
        contract=_make_contract(),
    )
    pool.claim_worker(crew_id, worker.worker_id)

    import pytest
    with pytest.raises(ValueError, match="Cannot claim"):
        pool.claim_worker(crew_id, worker.worker_id)


def test_release_worker_idempotent_when_not_busy(tmp_path):
    crew_id = "crew-1"
    recorder = CrewRecorder(tmp_path)
    recorder.create_crew(crew_id, repo=tmp_path, root_goal="test")
    pool = _make_pool(recorder=recorder, repo_root=tmp_path)

    worker = pool.ensure_worker(
        repo_root=tmp_path,
        crew=_make_crew(crew_id),
        contract=_make_contract(),
    )
    # Release without claim -- should be idempotent
    pool.release_worker(crew_id, worker.worker_id)

    details = recorder.read_crew(crew_id)
    w = [w for w in details["workers"] if w["worker_id"] == worker.worker_id][0]
    assert w["status"] == "running"  # unchanged


def test_find_compatible_worker_excludes_busy(tmp_path):
    crew_id = "crew-1"
    recorder = CrewRecorder(tmp_path)
    recorder.create_crew(crew_id, repo=tmp_path, root_goal="test")
    pool = _make_pool(recorder=recorder, repo_root=tmp_path)

    worker = pool.ensure_worker(
        repo_root=tmp_path,
        crew=_make_crew(crew_id),
        contract=_make_contract(),
    )
    pool.claim_worker(crew_id, worker.worker_id)

    compatible = pool.find_compatible_worker(crew_id, _make_contract())
    assert compatible is None


def test_find_compatible_worker_finds_idle(tmp_path):
    crew_id = "crew-1"
    recorder = CrewRecorder(tmp_path)
    recorder.create_crew(crew_id, repo=tmp_path, root_goal="test")
    pool = _make_pool(recorder=recorder, repo_root=tmp_path)

    worker = pool.ensure_worker(
        repo_root=tmp_path,
        crew=_make_crew(crew_id),
        contract=_make_contract(),
    )
    pool.claim_worker(crew_id, worker.worker_id)
    pool.release_worker(crew_id, worker.worker_id)

    compatible = pool.find_compatible_worker(crew_id, _make_contract())
    assert compatible is not None
    assert compatible["worker_id"] == worker.worker_id
```

Note: The `_make_pool`, `_make_crew`, `_make_contract` helpers should be added to the test file if not already present. Check existing test patterns in the file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/workers/test_pool.py -k "claim or release or idle or busy" -v`
Expected: FAIL with `AttributeError: 'WorkerPool' object has no attribute 'claim_worker'`

- [ ] **Step 3: Implement claim/release on WorkerPool**

Add to `pool.py` (after `stop_crew` method, around line 320):

```python
def claim_worker(self, crew_id: str, worker_id: str) -> None:
    """Transition worker from RUNNING/IDLE to BUSY."""
    worker = self._find_worker(crew_id, worker_id)
    current = worker.get("status", "running")
    if current not in {"running", "idle"}:
        raise ValueError(f"Cannot claim worker {worker_id} in status {current}")
    self._recorder.update_worker(crew_id, worker_id, {"status": "busy"})
    self._recorder.append_event(crew_id, CrewEvent(
        event_id=self._event_id_factory(),
        crew_id=crew_id,
        worker_id=worker_id,
        type="worker_claimed",
        status="completed",
    ))

def release_worker(self, crew_id: str, worker_id: str) -> None:
    """Transition worker from BUSY to IDLE (idempotent)."""
    worker = self._find_worker(crew_id, worker_id)
    if worker.get("status") != "busy":
        return  # idempotent
    self._recorder.update_worker(crew_id, worker_id, {"status": "idle"})
    self._recorder.append_event(crew_id, CrewEvent(
        event_id=self._event_id_factory(),
        crew_id=crew_id,
        worker_id=worker_id,
        type="worker_released",
        status="completed",
    ))
```

- [ ] **Step 4: Update find_compatible_worker to exclude BUSY**

In `pool.py`, `find_compatible_worker` (line 196), change the status check:
```python
# OLD (line 196):
if is_terminal_worker_status(worker.get("status", WorkerStatus.RUNNING.value)):
    continue

# NEW:
status = worker.get("status", "running")
if status not in {"running", "idle"}:
    continue
```

- [ ] **Step 5: Run claim/release tests**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/workers/test_pool.py -k "claim or release or idle or busy" -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/workers/pool.py tests/workers/test_pool.py
git commit -m "feat: add claim/release protocol to WorkerPool"
```

### 2.3 Update planner and decision_policy for BUSY exclusion

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/planner.py:47-48`
- Modify: `src/codex_claude_orchestrator/crew/decision_policy.py:208-222`
- Modify: `tests/v4/test_planner.py`

- [ ] **Step 1: Write test for planner rejecting BUSY workers**

Add to `tests/v4/test_planner.py`:

```python
def test_planner_rejects_busy_worker() -> None:
    selected = PlannerPolicy().select_worker(
        workers=[
            {
                "worker_id": "worker-busy",
                "status": "busy",
                "authority_level": "source_write",
                "capabilities": ["edit_source"],
                "write_scope": ["src/"],
            }
        ],
        required_authority="source_write",
        required_capabilities=["edit_source"],
        requested_write_scope=["src/"],
    )
    assert selected is None


def test_planner_accepts_idle_worker() -> None:
    selected = PlannerPolicy().select_worker(
        workers=[
            {
                "worker_id": "worker-idle",
                "status": "idle",
                "authority_level": "source_write",
                "capabilities": ["edit_source"],
                "write_scope": ["src/"],
            }
        ],
        required_authority="source_write",
        required_capabilities=["edit_source"],
        requested_write_scope=["src/"],
    )
    assert selected is not None
    assert selected["worker_id"] == "worker-idle"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/v4/test_planner.py -k "busy or idle" -v`
Expected: FAIL (busy worker is incorrectly accepted)

- [ ] **Step 3: Update _is_active in planner.py**

```python
# OLD (lines 47-48):
def _is_active(worker: dict[str, Any]) -> bool:
    return worker.get("status", "running") not in {"failed", "stopped"}

# NEW:
def _is_active(worker: dict[str, Any]) -> bool:
    return worker.get("status", "running") in {"running", "idle"}
```

- [ ] **Step 4: Update _has_source_write_worker in decision_policy.py**

```python
# OLD (lines 208-213):
def _has_source_write_worker(self, workers: list[dict]) -> bool:
    return any(
        worker.get("status") not in {"failed", "stopped"}
        and worker.get("authority_level") == AuthorityLevel.SOURCE_WRITE.value
        for worker in workers
    )

# NEW:
def _has_source_write_worker(self, workers: list[dict]) -> bool:
    return any(
        worker.get("status") in {"running", "idle"}
        and worker.get("authority_level") == AuthorityLevel.SOURCE_WRITE.value
        for worker in workers
    )
```

Also update `_source_write_worker_id` (lines 215-219) and `_has_capability` (lines 221-222) similarly.

- [ ] **Step 5: Run planner and decision_policy tests**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/v4/test_planner.py tests/crew/test_decision_policy.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/v4/planner.py src/codex_claude_orchestrator/crew/decision_policy.py tests/v4/test_planner.py
git commit -m "feat: exclude BUSY workers from planner and decision policy"
```

---

## Task 3: State Consolidation

### 3.1 Add transition_worker_status to CrewRecorder

**Files:**
- Modify: `src/codex_claude_orchestrator/state/crew_recorder.py`
- Modify: `tests/state/test_crew_recorder.py`

- [ ] **Step 1: Write failing test for transition_worker_status**

Add to `tests/state/test_crew_recorder.py`:

```python
def test_transition_worker_status_success(tmp_path):
    recorder = CrewRecorder(tmp_path)
    recorder.create_crew("crew-1", repo=tmp_path, root_goal="test")
    recorder.append_worker("crew-1", WorkerRecord(
        worker_id="w-1", crew_id="crew-1", role=WorkerRole.IMPLEMENTER,
        agent_profile="claude", native_session_id="n-1",
        terminal_session="t-1", terminal_pane="t-1:0",
        transcript_artifact="", turn_marker="",
        workspace_mode=WorkspaceMode.WORKTREE, workspace_path=tmp_path,
        status=WorkerStatus.RUNNING,
    ))

    result = recorder.transition_worker_status("crew-1", "w-1", "running", "busy")
    assert result is True

    details = recorder.read_crew("crew-1")
    worker = details["workers"][0]
    assert worker["status"] == "busy"


def test_transition_worker_status_wrong_expected(tmp_path):
    recorder = CrewRecorder(tmp_path)
    recorder.create_crew("crew-1", repo=tmp_path, root_goal="test")
    recorder.append_worker("crew-1", WorkerRecord(
        worker_id="w-1", crew_id="crew-1", role=WorkerRole.IMPLEMENTER,
        agent_profile="claude", native_session_id="n-1",
        terminal_session="t-1", terminal_pane="t-1:0",
        transcript_artifact="", turn_marker="",
        workspace_mode=WorkspaceMode.WORKTREE, workspace_path=tmp_path,
        status=WorkerStatus.RUNNING,
    ))

    result = recorder.transition_worker_status("crew-1", "w-1", "busy", "idle")
    assert result is False

    details = recorder.read_crew("crew-1")
    worker = details["workers"][0]
    assert worker["status"] == "running"  # unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/state/test_crew_recorder.py -k "transition" -v`
Expected: FAIL with `AttributeError: 'CrewRecorder' object has no attribute 'transition_worker_status'`

- [ ] **Step 3: Implement transition_worker_status**

Add to `crew_recorder.py` (after `update_worker`, around line 90):

```python
def transition_worker_status(
    self,
    crew_id: str,
    worker_id: str,
    expected_status: str,
    new_status: str,
) -> bool:
    """Atomically transition worker status with optimistic locking.

    Returns True if transition succeeded, False if current status
    doesn't match expected_status.
    """
    path = self._crew_dir(crew_id) / "workers.jsonl"
    workers = self._read_jsonl(path)
    for worker in workers:
        if worker["worker_id"] == worker_id:
            current = worker.get("status", "running")
            if current != expected_status:
                return False
            worker["status"] = new_status
            worker["updated_at"] = utc_now()
            self._write_jsonl(path, workers)
            return True
    return False
```

- [ ] **Step 4: Run transition tests**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/state/test_crew_recorder.py -k "transition" -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/state/crew_recorder.py tests/state/test_crew_recorder.py
git commit -m "feat: add transition_worker_status to CrewRecorder"
```

### 3.2 Update claim/release to use transition_worker_status

**Files:**
- Modify: `src/codex_claude_orchestrator/workers/pool.py`

- [ ] **Step 1: Update claim_worker to use atomic transition**

```python
# In pool.py, update claim_worker:
def claim_worker(self, crew_id: str, worker_id: str) -> None:
    """Transition worker from RUNNING/IDLE to BUSY."""
    worker = self._find_worker(crew_id, worker_id)
    current = worker.get("status", "running")
    if current not in {"running", "idle"}:
        raise ValueError(f"Cannot claim worker {worker_id} in status {current}")
    transitioned = self._recorder.transition_worker_status(
        crew_id, worker_id, expected_status=current, new_status="busy",
    )
    if not transitioned:
        raise ValueError(f"Claim race: worker {worker_id} changed status concurrently")
    self._recorder.append_event(crew_id, CrewEvent(
        event_id=self._event_id_factory(),
        crew_id=crew_id,
        worker_id=worker_id,
        type="worker_claimed",
        status="completed",
    ))

def release_worker(self, crew_id: str, worker_id: str) -> None:
    """Transition worker from BUSY to IDLE (idempotent)."""
    transitioned = self._recorder.transition_worker_status(
        crew_id, worker_id, expected_status="busy", new_status="idle",
    )
    if not transitioned:
        return  # idempotent
    self._recorder.append_event(crew_id, CrewEvent(
        event_id=self._event_id_factory(),
        crew_id=crew_id,
        worker_id=worker_id,
        type="worker_released",
        status="completed",
    ))
```

- [ ] **Step 2: Run all pool tests**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/workers/test_pool.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/codex_claude_orchestrator/workers/pool.py
git commit -m "refactor: claim/release uses transition_worker_status for atomicity"
```

### 3.3 Derive active_worker_ids from worker records

**Files:**
- Modify: `src/codex_claude_orchestrator/state/crew_recorder.py`
- Modify: `src/codex_claude_orchestrator/workers/pool.py`
- Modify: `src/codex_claude_orchestrator/crew/controller.py`

- [ ] **Step 1: Add active_worker_ids method to CrewRecorder**

Add to `crew_recorder.py`:

```python
def active_worker_ids(self, crew_id: str) -> list[str]:
    """Derive active worker IDs from worker records.

    A worker is active if its status is not terminal (FAILED or STOPPED).
    """
    from codex_claude_orchestrator.crew.models import is_terminal_worker_status
    path = self._crew_dir(crew_id) / "workers.jsonl"
    workers = self._read_jsonl(path)
    return [
        w["worker_id"] for w in workers
        if not is_terminal_worker_status(w.get("status", "running"))
    ]
```

- [ ] **Step 2: Update find_compatible_worker to use derived active_worker_ids**

In `pool.py`, `find_compatible_worker` (line 191):
```python
# OLD:
active_worker_ids = set(details["crew"].get("active_worker_ids") or [])

# NEW:
active_worker_ids = set(self._recorder.active_worker_ids(crew_id))
```

- [ ] **Step 3: Remove _add_active_worker_id and _remove_active_worker_ids from pool.py**

Delete methods `_add_active_worker_id` (lines 394-399) and `_remove_active_worker_ids` (lines 388-392).

Update `ensure_worker` (line 173): remove the call to `self._add_active_worker_id(crew.crew_id, worker_id)`.

Update `stop_worker` (line 310): remove the call to `self._remove_active_worker_ids(crew_id, [worker_id])`.

Update `stop_crew` (line 319): remove `self._recorder.update_crew(crew_id, {"active_worker_ids": []})`.

- [ ] **Step 4: Remove duplicate active_worker_ids from controller.py**

In `controller.py`, `ensure_worker` (lines 167-170): remove the block that independently adds to `active_worker_ids`.

In `controller.py`, `stop` (line 403): remove `self._recorder.update_crew(crew_id, {"active_worker_ids": []})`.

In `controller.py`, `stop_workers_for_accept` (line 409): remove `self._recorder.update_crew(crew_id, {"active_worker_ids": []})`.

- [ ] **Step 5: Run all tests**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest --tb=short`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/state/crew_recorder.py src/codex_claude_orchestrator/workers/pool.py src/codex_claude_orchestrator/crew/controller.py
git commit -m "refactor: derive active_worker_ids from worker records, remove redundant cache"
```

---

## Task 4: V4 Supervisor Integration

### 4.1 Add claim/release to CrewController

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/controller.py`
- Modify: `tests/crew/test_controller.py`

- [ ] **Step 1: Add claim_worker/release_worker to CrewController**

In `controller.py`, add methods (after `ensure_worker`, around line 175):

```python
def claim_worker(self, crew_id: str, worker_id: str) -> None:
    """Transition worker to BUSY. Delegates to worker pool."""
    self._worker_pool.claim_worker(crew_id, worker_id)

def release_worker(self, crew_id: str, worker_id: str) -> None:
    """Transition worker to IDLE. Delegates to worker pool."""
    self._worker_pool.release_worker(crew_id, worker_id)
```

- [ ] **Step 2: Run controller tests**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/crew/test_controller.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/codex_claude_orchestrator/crew/controller.py
git commit -m "feat: add claim/release pass-through to CrewController"
```

### 4.2 Integrate claim/release into V4CrewRunner.supervise

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/crew_runner.py`
- Modify: `tests/v4/test_crew_runner.py`

- [ ] **Step 1: Write integration test for claim/release around turns**

Add to `tests/v4/test_crew_runner.py`:

```python
def test_v4_crew_runner_claims_and_releases_source_worker(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    controller = FakeController([{"passed": True, "summary": "command passed"}])
    supervisor = FakeV4Supervisor(
        [
            {"status": "turn_completed", "turn_id": "round-1-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-1-worker-review-review"},
        ],
        event_store=store,
        review_summaries=[
            "<<<CODEX_REVIEW\nverdict: OK\nsummary: patch matches spec\nfindings:\n>>>"
        ],
    )

    V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=store,
    ).supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
        max_rounds=1,
    )

    # Verify claim/release was called for source worker
    assert ("crew-1", "worker-source") in controller.claimed
    assert ("crew-1", "worker-source") in controller.released
```

Update `FakeController` in the test file to track claim/release calls:

```python
class FakeController:
    def __init__(self, verification_results, workers=None):
        # ... existing init ...
        self.claimed = []
        self.released = []

    def claim_worker(self, crew_id, worker_id):
        self.claimed.append((crew_id, worker_id))

    def release_worker(self, crew_id, worker_id):
        self.released.append((crew_id, worker_id))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/v4/test_crew_runner.py -k "claims_and_releases" -v`
Expected: FAIL (claim/release not yet called in supervise)

- [ ] **Step 3: Add claim/release calls to V4CrewRunner.supervise**

In `crew_runner.py`, find the source turn execution in `supervise`. The pattern should wrap `run_source_turn`:

```python
# Find the section where the source worker turn is executed.
# It looks approximately like:
#   turn_result = self._supervisor.run_source_turn(...)

# Wrap with claim/release:
source_worker_id = source_worker["worker_id"]
self._controller.claim_worker(crew_id, source_worker_id)
try:
    turn_result = self._supervisor.run_source_turn(...)
finally:
    self._controller.release_worker(crew_id, source_worker_id)
```

Apply the same pattern for review worker turns (`run_worker_turn` with `phase="review"`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest tests/v4/test_crew_runner.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/crew_runner.py tests/v4/test_crew_runner.py
git commit -m "feat: integrate claim/release into V4 supervisor turn lifecycle"
```

### 4.3 Full regression test

- [ ] **Step 1: Run entire test suite**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && python -m pytest --tb=short -q`
Expected: ALL 588+ tests PASS, no regressions

- [ ] **Step 2: Verify no remaining references to old scope functions**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && grep -rn "_scope_covers\|_write_scope_covers\|_is_in_scope" src/ --include="*.py" | grep -v "scope.py" | grep -v "__pycache__"`
Expected: No matches (all old scope functions removed)

- [ ] **Step 3: Verify no remaining inline terminal status sets**

Run: `cd /Users/zhanghaoqian/Documents/zhangzhang/agent/channel && grep -rn '"failed", "stopped"' src/ --include="*.py" | grep -v "__pycache__"`
Expected: No matches (all replaced with `is_terminal_worker_status` or explicit `{"running", "idle"}` checks)

---

## Summary

| Task | Description | Estimated Steps |
|------|-------------|----------------|
| 1.1 | Create scope.py with tests (TDD) | 5 |
| 1.2 | Migrate pool.py | 3 |
| 1.3 | Migrate planner.py | 3 |
| 1.4 | Migrate gates.py | 3 |
| 1.5 | Migrate crew_runner.py (fix bug) | 3 |
| 1.6 | Full regression | 1 |
| 2.1 | Add BUSY status | 3 |
| 2.2 | Add claim/release to WorkerPool | 6 |
| 2.3 | Update planner + decision_policy | 6 |
| 3.1 | Add transition_worker_status | 5 |
| 3.2 | Update claim/release for atomicity | 3 |
| 3.3 | Derive active_worker_ids | 6 |
| 4.1 | Add claim/release to CrewController | 3 |
| 4.2 | V4 supervisor integration | 5 |
| 4.3 | Full regression | 3 |
| **Total** | | **58 steps** |
