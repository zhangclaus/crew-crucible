# Worker Lifecycle Unification Design

Date: 2026-05-03
Status: Approved (方案 B: 完整生命周期统一)

## Purpose

This design addresses four process/workflow defects in the current worker
lifecycle management, identified through comparison with multica-ai/multica's
agent-as-teammate patterns:

1. **No claim/release protocol** -- workers cannot signal "idle" or "busy",
   making reuse purely heuristic.
2. **Three incompatible write-scope checks** -- `V4CrewRunner._scope_covers`,
   `PlannerPolicy._scope_covers`, and `WriteScopeGate._is_in_scope` use
   different normalization and matching logic.
3. **Scattered state sources** -- worker status lives in `workers.jsonl`,
   `crew.json` (`active_worker_ids`), and in-memory adapter dicts with no
   single source of truth.
4. **`IDLE` status defined but never set** -- workers stay `RUNNING` forever
   until killed, preventing pool-based reuse.

## Context

### Current Worker Lifecycle

```
spawn(RUNNING) ──► send_turn ──► observe ──► [repeat] ──► stop(STOPPED)
                                                         └──► fail(FAILED)
```

Workers transition from `RUNNING` directly to `STOPPED` or `FAILED`. There is
no intermediate state. The `IDLE` enum value exists in `WorkerStatus` but no
code path ever sets it.

Worker reuse in `find_compatible_worker` (pool.py:189-207) checks:
- worker is in `active_worker_ids`
- status is not terminal
- capabilities, authority, workspace_mode, write_scope all match

But it does NOT check whether the worker is currently busy executing a turn.
This means two concurrent callers could get the same "compatible" worker.

### Three Write-Scope Implementations

| Location | Method | Normalization | Matching |
|----------|--------|---------------|----------|
| `pool.py:543` | `_scope_covers` | `strip().lstrip("./")` | `target.startswith(f"{allowed}/")` |
| `planner.py:65` | `_scope_covers` | `strip().lstrip("./")` | `Path.is_relative_to(allowed_path)` |
| `gates.py:118` | `_is_in_scope` | `replace("\\", "/"), strip("./")` | `path.startswith(scope)` or `path == scope` |
| `crew_runner.py:594` | `_scope_covers` | `replace("\\", "/") + trailing "/"` | bidirectional prefix: `a.startswith(b)` OR `b.startswith(a)` |

The bidirectional prefix check in `crew_runner.py` is semantically wrong:
`src/` would "cover" `src/app/` AND `src/app/` would "cover" `src/`. This
means a worker with `write_scope=["docs/"]` would incorrectly be considered
compatible with a request for `write_scope=["docs/api/"]` AND vice versa.

### State Scatter

| Source | Contains | Updated By |
|--------|----------|------------|
| `workers.jsonl` | Full worker records incl. status | `CrewRecorder.update_worker` |
| `crew.json` | `active_worker_ids` list | `WorkerPool._add/_remove_active_worker_id` |
| `ClaudeCodeTmuxAdapter._workers` | `dict[str, WorkerSpec]` in memory | `register_worker` |
| EventStore | `turn.completed`, `turn.failed` events | Various |

When the V4 supervisor runs, it reads from the recorder (filesystem), the
adapter reads from its memory dict, and the decision policy reads from the
snapshot (which comes from the recorder). If any of these diverge, the system
can make inconsistent decisions.

## Goals

1. Introduce a **claim/release protocol** so workers explicitly transition
   between BUSY and IDLE states.
2. **Unify write-scope checking** into a single module with consistent
   normalization and matching semantics.
3. **Consolidate worker state** with `workers.jsonl` (via `CrewRecorder`) as the
   single source of truth, eliminating the redundant `active_worker_ids` cache in
   `crew.json` and making the adapter dict a read-through cache.
4. **Activate `IDLE` status** so completed workers return to a reusable pool
   instead of staying `RUNNING` until killed.

## Non-Goals

- Do not implement cross-crew worker reuse (workers remain crew-scoped).
- Do not change the tmux-based execution model (each worker is still a tmux
  session).
- Do not implement warm-start or worker process pooling.
- Do not change the V3 supervisor loop (only V4 path is affected).
- Do not redesign the EventStore schema (reuse existing event types).

## Design

### 1. Unified Write-Scope Module

Create `src/codex_claude_orchestrator/crew/scope.py` as the single source of
truth for all write-scope operations.

```python
# scope.py

def normalize_path(path: str) -> str:
    """Normalize a path for scope comparison.
    - Forward slashes only
    - Strip leading ./ and /
    - Ensure trailing / for directory matching
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
    """
    target = normalize_path(target_path)
    if not target:
        return True
    for scope in write_scope:
        s = normalize_path(scope)
        if not s:
            continue
        # Ensure directory prefix matching
        if not s.endswith("/"):
            s += "/"
        if not target.endswith("/"):
            target_with_slash = target + "/"
        else:
            target_with_slash = target
        if target_with_slash.startswith(s) or target == normalize_path(scope):
            return True
    return False


def scope_covers_all(write_scope: list[str], target_paths: list[str]) -> bool:
    """Check if write_scope covers ALL target paths."""
    return all(scope_covers(write_scope, p) for p in target_paths if p)


def is_protected(path: str, protected_patterns: list[str]) -> bool:
    """Check if a path matches any protected pattern."""
    from fnmatch import fnmatch
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

**Migration plan:**
- Replace `_scope_covers` in `pool.py` with `from .scope import scope_covers`
- Replace `_scope_covers` in `planner.py` with same import
- Replace `_is_in_scope` in `gates.py` with `from .scope import scope_covers`
- Replace `_scope_covers` in `crew_runner.py` with same import
- All callers pass `list[str]` and a single target path

**Critical fix:** The bidirectional prefix check in `crew_runner.py:603` is
removed. The new `scope_covers` uses strict unidirectional prefix matching:
`scope` must be a parent of (or equal to) `target`.

### 2. Claim/Release Protocol

Add two new event types and a worker state machine:

```
                   claim_task
RUNNING ──────────────────────────► BUSY
   ▲                                  │
   │           release_worker         │
   │◄─────────────────────────────────┘
   │
   │  stop_worker
   ▼
STOPPED ◄────── FAILED ◄──── BUSY (on error)
```

**New WorkerStatus values:**
```python
class WorkerStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"    # idle, waiting for assignment
    BUSY = "busy"          # actively executing a turn
    IDLE = "idle"          # completed turn, available for reuse
    FAILED = "failed"
    STOPPED = "stopped"
```

Note: `RUNNING` semantics change from "alive and doing stuff" to "alive and
available". `BUSY` replaces the old "actively working" meaning. `IDLE` is
synonymous with `RUNNING` in the new model but explicitly signals "just
finished a turn, ready for next assignment".

**New methods on `WorkerPool`:**

```python
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
        return  # idempotent: already released or in different status
    self._recorder.append_event(crew_id, CrewEvent(
        event_id=self._event_id_factory(),
        crew_id=crew_id,
        worker_id=worker_id,
        type="worker_released",
        status="completed",
    ))
```

**Integration with V4 supervisor:**

In `V4CrewRunner.supervise`, the turn lifecycle becomes:

```python
# Before sending turn:
self._pool.claim_worker(crew_id, worker_id)

# Send turn and wait for completion:
result = self._supervisor.run_turn(...)

# After turn completes (success or failure):
self._pool.release_worker(crew_id, worker_id)
```

**Integration with `find_compatible_worker`:**

```python
def find_compatible_worker(self, crew_id: str, contract: WorkerContract) -> dict | None:
    details = self._recorder.read_crew(crew_id)
    active_worker_ids = set(details["crew"].get("active_worker_ids") or [])
    required = set(contract.required_capabilities)
    for worker in details["workers"]:
        if worker["worker_id"] not in active_worker_ids:
            continue
        status = worker.get("status", "running")
        # Only IDLE or RUNNING workers can be reused
        if status not in {"running", "idle"}:
            continue
        if not required.issubset(set(worker.get("capabilities", []))):
            continue
        # ... rest of checks unchanged
        return worker
    return None
```

### 3. State Consolidation

**Principle:** `workers.jsonl` via `CrewRecorder` remains the persistence layer,
but worker status transitions are now atomic and event-sourced.

**Changes to `CrewRecorder`:**

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
    workers = self._read_workers(crew_id)
    for worker in workers:
        if worker["worker_id"] == worker_id:
            current = worker.get("status", "running")
            if current != expected_status:
                return False
            worker["status"] = new_status
            worker["updated_at"] = utc_now()
            self._write_workers(crew_id, workers)
            return True
    return False
```

**Remove `active_worker_ids` from `crew.json`:**

The `active_worker_ids` list in `crew.json` is a denormalized cache that can
drift out of sync. Instead, derive the active set from the workers themselves:

```python
def active_worker_ids(self, crew_id: str) -> list[str]:
    """Derive active worker IDs from worker records."""
    return [
        w["worker_id"] for w in self._read_workers(crew_id)
        if not is_terminal_worker_status(w.get("status", "running"))
    ]
```

This eliminates the `_add_active_worker_id` / `_remove_active_worker_id`
methods and the associated `crew.json` updates.

**Adapter state synchronization:**

`ClaudeCodeTmuxAdapter._workers` becomes a read-through cache backed by the
recorder. When the adapter needs worker info, it reads from the recorder
instead of maintaining its own dict. The `register_worker` method still adds
to the in-memory dict for performance, but the dict is not the source of truth.

### 4. IDLE State Activation

With the claim/release protocol, workers automatically transition to `IDLE`
after completing a turn. The V4 supervisor's `run_turn` method wraps this:

```python
class V4Supervisor:
    def run_turn(self, *, crew_id, worker_id, message, ...):
        self._pool.claim_worker(crew_id, worker_id)
        try:
            # ... existing turn execution logic ...
            return result
        finally:
            self._pool.release_worker(crew_id, worker_id)
```

**Worker reuse after IDLE:**

When `_source_worker` (crew_runner.py:562) or `_review_worker` (crew_runner.py:607)
call `PlannerPolicy.select_worker`, the policy now sees `IDLE` workers as
candidates (they pass the `_is_active` check since `idle not in {"failed", "stopped"}`).

The `ensure_worker` path in `WorkerPool` also benefits: `find_compatible_worker`
now returns `IDLE` workers, preventing unnecessary spawns.

**Worker pool lifecycle:**

```
spawn ──► RUNNING ──► claim ──► BUSY ──► release ──► IDLE ──► claim ──► BUSY ...
                                                          │
                                                          └──► stop ──► STOPPED
```

Workers can cycle between IDLE and BUSY multiple times within a crew's
lifetime. They are only stopped when:
- The crew is finalized (accept/stop/fail)
- `stop_worker` is explicitly called
- The worker fails (transitions to FAILED)

## Implementation Plan

### Phase 1: Unified Write-Scope Module

1. Create `src/codex_claude_orchestrator/crew/scope.py` with `normalize_path`,
   `scope_covers`, `scope_covers_all`, `is_protected`.
2. Replace all 4 existing implementations with imports from `scope.py`.
3. Fix the bidirectional prefix bug in `crew_runner.py`.
4. Add unit tests for edge cases: trailing slashes, `./` prefixes, Windows
   backslashes, exact matches, subdirectory matches.
5. Verify all existing tests pass.

### Phase 2: Claim/Release Protocol

1. Add `BUSY` to `WorkerStatus` enum.
2. Add `claim_worker` and `release_worker` to `WorkerPool`.
3. Update `find_compatible_worker` to only match `RUNNING` or `IDLE` workers.
4. Update `_is_active` in `planner.py` to include `IDLE` check.
5. Update `_has_source_write_worker` in `decision_policy.py` to exclude `BUSY`.
6. Add unit tests for claim/release transitions and compatibility filtering.

### Phase 3: State Consolidation

1. Add `transition_worker_status` to `CrewRecorder`.
2. Replace `_add_active_worker_id` / `_remove_active_worker_id` with derived
   `active_worker_ids()` method.
3. Remove `active_worker_ids` field from `CrewRecord` (or deprecate it).
4. Update all callers that read `active_worker_ids` from `crew.json`.
5. Verify JSONL read/write atomicity under concurrent access.

### Phase 4: V4 Supervisor Integration

1. Wrap `V4Supervisor.run_turn` with claim/release.
2. Ensure `release_worker` is called in `finally` block (even on exception).
3. Update `V4CrewRunner.supervise` to use new lifecycle.
4. Integration test: worker reuse across multiple rounds.

## Risks

| Risk | Mitigation |
|------|------------|
| Optimistic locking race on JSONL | Single-threaded supervisor; no concurrent writers |
| IDLE workers with stale tmux sessions | `status_worker` check before reuse; prune_orphans |
| Breaking V3 supervisor loop | Changes are additive; V3 code path unchanged |
| Write-scope unification changes behavior | Comprehensive test suite; the bidirectional fix is a bugfix |

## Test Strategy

- Unit tests for `scope.py`: 20+ edge cases covering normalization and matching
- Unit tests for claim/release: status transitions, error cases, idempotency
- Unit tests for `transition_worker_status`: optimistic locking, concurrent scenarios
- Integration test: full supervise loop with worker reuse across 3+ rounds
- Regression: all 588 existing tests continue to pass
