# Bridge Auto Supervisor Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic background supervision loop for supervised Claude bridge sessions.

**Architecture:** Introduce a small `BridgeSupervisorLoop` orchestration class that composes the existing `ClaudeBridge` methods. CLI adds `claude bridge supervise` for existing bridges and `claude bridge run` for start-plus-supervise. Verification remains delegated to `VerificationRunner` and `PolicyGate`.

**Tech Stack:** Python 3.13, argparse CLI, pytest, existing `ClaudeBridge` and `VerificationRunner`.

---

### Task 1: Bridge Supervisor Loop

**Files:**
- Create: `src/codex_claude_orchestrator/bridge_supervisor_loop.py`
- Test: `tests/test_bridge_supervisor_loop.py`

- [ ] **Step 1: Write failing tests**

Create tests covering:

```python
def test_supervisor_accepts_after_verification_passes(tmp_path):
    # start supervised bridge with fake Claude success
    # supervise with one passing verification command
    # assert bridge accepted and one verify event exists

def test_supervisor_challenges_failed_verification_until_acceptance(tmp_path):
    # fake Claude initial success, failed verification, repair success, passing verification
    # assert challenge event then accept event

def test_supervisor_marks_needs_human_after_round_budget(tmp_path):
    # fake verification always fails
    # assert needs_human after max_rounds
```

Run:

```bash
.venv/bin/python -m pytest tests/test_bridge_supervisor_loop.py -v
```

Expected: fail because the module does not exist.

- [ ] **Step 2: Implement minimal loop**

Implement `BridgeSupervisorLoop.supervise(...)` with injected `sleep` callable and event JSON output. Use `bridge.status`, `bridge.verify`, `bridge.challenge(send=True)`, `bridge.accept`, and `bridge.needs_human`.

- [ ] **Step 3: Verify loop tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_bridge_supervisor_loop.py -v
```

Expected: pass.

### Task 2: CLI Wiring

**Files:**
- Modify: `src/codex_claude_orchestrator/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI route tests**

Extend `test_claude_bridge_commands_route_to_bridge` or add a focused test proving:

```bash
orchestrator claude bridge supervise --repo <repo> --verification-command "pytest -q" --max-rounds 2
orchestrator claude bridge run --repo <repo> --goal "x" --workspace-mode shared --verification-command "pytest -q" --max-rounds 2
```

route to the loop builder with expected arguments.

- [ ] **Step 2: Add parser entries**

Add `supervise` and `run` subcommands with `--repo`, optional `--bridge-id`, repeatable `--verification-command`, `--max-rounds`, `--poll-interval`, and `--visual`/`--workspace-mode` for `run`.

- [ ] **Step 3: Add handlers**

Build the bridge via `build_claude_bridge(repo_root)`, wrap it in `BridgeSupervisorLoop`, and print returned JSON.

- [ ] **Step 4: Verify CLI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_claude_bridge_commands_route_to_bridge -v
```

Expected: pass.

### Task 3: Final Verification

**Files:**
- No new production files beyond Tasks 1-2.

- [ ] **Step 1: Run targeted tests**

```bash
.venv/bin/python -m pytest tests/test_bridge_supervisor_loop.py tests/test_claude_bridge.py tests/test_cli.py -v
```

- [ ] **Step 2: Run full tests**

```bash
.venv/bin/python -m pytest -v
```

- [ ] **Step 3: Commit**

```bash
git add src/codex_claude_orchestrator/bridge_supervisor_loop.py src/codex_claude_orchestrator/cli.py tests/test_bridge_supervisor_loop.py tests/test_cli.py docs/superpowers/specs/2026-04-29-bridge-auto-supervisor-loop-design.zh.md docs/superpowers/plans/2026-04-29-bridge-auto-supervisor-loop.md
git commit -m "feat: add bridge supervisor loop"
```
