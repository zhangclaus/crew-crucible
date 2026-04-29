# Codex-in-the-loop Claude Bridge Supervision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add supervised Claude bridge commands so the current Codex App can inspect Claude output, run verification, record challenges, and continue the same Claude `--resume` conversation.

**Architecture:** Keep `ClaudeBridge` as the owner of Claude long-dialogue state and add optional V2 session mirroring through `SessionRecorder`. Supervised bridges create a linked `SessionRecord`, mirror Claude turns into `TurnRecord` and `OutputTrace`, expose `status`, `verify`, `challenge`, `accept`, and `needs-human`, and leave unsupervised bridge behavior unchanged.

**Tech Stack:** Python 3.11+, stdlib JSON/pathlib/subprocess/shlex/uuid, existing `SessionRecorder`, `VerificationRunner`, `PolicyGate`, `ResultEvaluator`, pytest.

---

## Scope Check

This plan implements the Codex-in-the-loop control surface only. It does not automate the current Codex App UI, does not create a background Codex controller, and does not replace `SessionEngine`.

## File Structure

- Modify: `src/codex_claude_orchestrator/claude_bridge.py`
  - Add optional supervision dependencies.
  - Create linked V2 sessions for supervised bridges.
  - Mirror Claude bridge turns into session turns and output traces.
  - Add `status`, `verify`, `challenge`, `accept`, and `needs_human` methods.
- Modify: `src/codex_claude_orchestrator/cli.py`
  - Wire supervised bridge parser flags and new bridge subcommands.
  - Build `SessionRecorder` and `VerificationRunner` for bridge supervision.
- Modify: `tests/test_claude_bridge.py`
  - Add core supervised bridge tests.
  - Keep existing unsupervised tests passing.
- Modify: `tests/test_cli.py`
  - Add CLI routing tests for supervised bridge commands.
- Read-only reference: `docs/superpowers/specs/2026-04-29-codex-in-loop-claude-bridge-supervision-design.md`

## Task 1: Supervised Bridge Start and Session Mirroring

**Files:**
- Modify: `src/codex_claude_orchestrator/claude_bridge.py`
- Modify: `tests/test_claude_bridge.py`

- [x] **Step 1: Write the failing supervised start test**

Append this test to `tests/test_claude_bridge.py`:

```python
def test_bridge_start_supervised_creates_session_and_mirrors_initial_turn(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")

    def fake_runner(command, **kwargs):
        return CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"初始实现完成。"}',
            stderr="",
        )

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-supervised",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-bridge",
        task_id_factory=lambda: "task-bridge",
        trace_id_factory=lambda: "trace-start",
    )

    result = bridge.start(
        repo_root=repo_root,
        goal="实现 Codex 监督 bridge",
        workspace_mode="shared",
        supervised=True,
    )

    assert result["bridge"]["supervised"] is True
    assert result["bridge"]["session_id"] == "session-bridge"
    assert result["bridge"]["latest_turn_id"] == "turn-start"
    assert result["latest_turn"]["result_text"] == "初始实现完成。"

    details = recorder.read_session("session-bridge")
    assert details["session"]["goal"] == "实现 Codex 监督 bridge"
    assert details["session"]["assigned_agent"] == "claude"
    assert details["session"]["workspace_mode"] == "shared"
    assert details["turns"][0]["turn_id"] == "turn-start"
    assert details["turns"][0]["phase"] == "execute"
    assert details["turns"][0]["from_agent"] == "claude"
    assert details["turns"][0]["to_agent"] == "codex"
    assert details["output_traces"][0]["trace_id"] == "trace-start"
    assert details["output_traces"][0]["run_id"] == "turn-start"
    assert details["output_traces"][0]["command"][0:2] == ["claude", "--print"]
    assert details["output_traces"][0]["stdout_artifact"].endswith("bridge/turn-start/stdout.txt")
    assert details["output_traces"][0]["stderr_artifact"].endswith("bridge/turn-start/stderr.txt")
    assert details["output_traces"][0]["evaluation"]["accepted"] is True
```

Add the imports at the top of `tests/test_claude_bridge.py`:

```python
from codex_claude_orchestrator.session_recorder import SessionRecorder
```

- [x] **Step 2: Run the focused test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_claude_bridge.py::test_bridge_start_supervised_creates_session_and_mirrors_initial_turn -v
```

Expected: FAIL with `TypeError: ClaudeBridge.__init__() got an unexpected keyword argument 'session_recorder'`.

- [x] **Step 3: Add supervision dependencies and id factories**

Modify imports in `src/codex_claude_orchestrator/claude_bridge.py`:

```python
from codex_claude_orchestrator.models import (
    EvaluationOutcome,
    OutputTrace,
    SessionRecord,
    SessionStatus,
    TurnPhase,
    TurnRecord,
    VerificationRecord,
    WorkerResult,
    WorkspaceMode,
    utc_now,
)
from codex_claude_orchestrator.result_evaluator import ResultEvaluator
from codex_claude_orchestrator.session_recorder import SessionRecorder
```

Extend `ClaudeBridge.__init__`:

```python
def __init__(
    self,
    state_root: Path,
    *,
    runner: CommandRunner | None = None,
    visual_runner: CommandRunner | None = None,
    bridge_id_factory: Callable[[], str] | None = None,
    turn_id_factory: Callable[[], str] | None = None,
    session_recorder: SessionRecorder | None = None,
    verification_runner: Any | None = None,
    result_evaluator: ResultEvaluator | None = None,
    session_id_factory: Callable[[], str] | None = None,
    task_id_factory: Callable[[], str] | None = None,
    trace_id_factory: Callable[[], str] | None = None,
    challenge_id_factory: Callable[[], str] | None = None,
):
    self._state_root = state_root
    self._bridges_root = state_root / "claude-bridge"
    self._bridges_root.mkdir(parents=True, exist_ok=True)
    self._runner = runner or subprocess.run
    self._visual_runner = visual_runner or subprocess.run
    self._bridge_id_factory = bridge_id_factory or (lambda: f"bridge-{uuid4().hex}")
    self._turn_id_factory = turn_id_factory or (lambda: f"turn-{uuid4().hex}")
    self._session_recorder = session_recorder or SessionRecorder(state_root)
    self._verification_runner = verification_runner
    self._result_evaluator = result_evaluator or ResultEvaluator()
    self._session_id_factory = session_id_factory or (lambda: f"session-{uuid4().hex}")
    self._task_id_factory = task_id_factory or (lambda: f"task-{uuid4().hex}")
    self._trace_id_factory = trace_id_factory or (lambda: f"trace-{uuid4().hex}")
    self._challenge_id_factory = challenge_id_factory or (lambda: f"challenge-{uuid4().hex}")
```

- [x] **Step 4: Add `supervised` to `start` and create the linked session**

Change the `start` signature:

```python
def start(
    self,
    *,
    repo_root: Path,
    goal: str,
    workspace_mode: str = "readonly",
    visual: str = "none",
    dry_run: bool = False,
    supervised: bool = False,
) -> dict[str, Any]:
```

After the base `record` dict is created, add:

```python
if supervised:
    session = self._create_supervised_session(
        repo=repo,
        goal=goal,
        workspace_mode=workspace_mode,
    )
    record.update(
        {
            "supervised": True,
            "session_id": session.session_id,
            "root_task_id": session.root_task_id,
            "latest_turn_id": None,
            "latest_verification_status": None,
            "latest_challenge_id": None,
        }
    )
```

Add this helper:

```python
def _create_supervised_session(self, *, repo: Path, goal: str, workspace_mode: str) -> SessionRecord:
    session = SessionRecord(
        session_id=self._session_id_factory(),
        root_task_id=self._task_id_factory(),
        repo=str(repo),
        goal=goal,
        assigned_agent="claude",
        workspace_mode=WorkspaceMode(workspace_mode),
        max_rounds=1,
    )
    self._session_recorder.start_session(session)
    return session
```

- [x] **Step 5: Mirror Claude turns into the linked V2 session**

After `turn = self._run_turn(...)` in `start`, before `_advance_record`, add:

```python
if record.get("supervised") and record.get("session_id"):
    self._mirror_bridge_turn(record, turn)
```

Add the same block in `send`, after `_run_turn(...)` and before `_advance_record(...)`.

Add these helpers:

```python
def _mirror_bridge_turn(self, record: dict[str, Any], turn: dict[str, Any]) -> None:
    session_id = str(record["session_id"])
    root_task_id = str(record["root_task_id"])
    stdout_path = self._session_recorder.write_text_artifact(
        session_id,
        f"bridge/{turn['turn_id']}/stdout.txt",
        str(turn.get("stdout") or ""),
    )
    stderr_path = self._session_recorder.write_text_artifact(
        session_id,
        f"bridge/{turn['turn_id']}/stderr.txt",
        str(turn.get("stderr") or ""),
    )
    evaluation = self._evaluate_bridge_turn(turn)
    summary = str(turn.get("result_text") or turn.get("parse_error") or evaluation.summary)
    turn_record = TurnRecord(
        turn_id=str(turn["turn_id"]),
        session_id=session_id,
        round_index=int(record.get("turn_count", 0)) + 1,
        phase=TurnPhase.EXECUTE,
        task_id=root_task_id,
        run_id=str(turn["turn_id"]),
        from_agent="claude",
        to_agent="codex",
        message=str(turn.get("message") or ""),
        decision=evaluation.next_action.value,
        summary=summary,
        payload={"bridge_turn": turn, "evaluation": evaluation.to_dict()},
    )
    trace = OutputTrace(
        trace_id=self._trace_id_factory(),
        session_id=session_id,
        turn_id=str(turn["turn_id"]),
        run_id=str(turn["turn_id"]),
        task_id=root_task_id,
        output_summary=summary,
        agent="claude",
        adapter="ClaudeBridge",
        command=list(turn.get("command") or []),
        stdout_artifact=str(stdout_path),
        stderr_artifact=str(stderr_path),
        display_summary=summary,
        artifact_paths=[str(stdout_path), str(stderr_path)],
        evaluation=evaluation,
    )
    self._session_recorder.append_turn(session_id, turn_record)
    self._session_recorder.append_output_trace(session_id, trace)
```

Add mechanical evaluation helper:

```python
def _evaluate_bridge_turn(self, turn: dict[str, Any]) -> EvaluationOutcome:
    result_text = str(turn.get("result_text") or "").strip()
    structured_output = None
    if int(turn.get("returncode", 0)) == 0 and not turn.get("parse_error") and result_text:
        structured_output = {
            "summary": result_text,
            "status": "completed",
            "changed_files": [],
            "verification_commands": [],
            "notes_for_supervisor": [],
        }
    return self._result_evaluator.evaluate(
        WorkerResult(
            raw_output=str(turn.get("stdout") or ""),
            stdout=str(turn.get("stdout") or ""),
            stderr=str(turn.get("stderr") or ""),
            exit_code=int(turn.get("returncode", 0)),
            structured_output=structured_output,
            parse_error=str(turn["parse_error"]) if turn.get("parse_error") else None,
        )
    )
```

- [x] **Step 6: Update record advancement for supervised metadata**

In `_advance_record`, after updating `turn_count`, add:

```python
if updated.get("supervised"):
    updated["latest_turn_id"] = turn["turn_id"]
```

- [x] **Step 7: Run the focused test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_claude_bridge.py::test_bridge_start_supervised_creates_session_and_mirrors_initial_turn -v
```

Expected: PASS.

- [x] **Step 8: Run existing bridge tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_claude_bridge.py -v
```

Expected: PASS.

- [x] **Step 9: Commit**

Run:

```bash
git add src/codex_claude_orchestrator/claude_bridge.py tests/test_claude_bridge.py
git commit -m "feat: mirror supervised bridge turns"
```

## Task 2: Bridge Status and Verification Commands

**Files:**
- Modify: `src/codex_claude_orchestrator/claude_bridge.py`
- Modify: `tests/test_claude_bridge.py`

- [x] **Step 1: Write failing tests for `status` and `verify`**

Append these tests to `tests/test_claude_bridge.py`:

```python
def test_bridge_status_returns_supervision_snapshot(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-status",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-status",
        task_id_factory=lambda: "task-status",
        trace_id_factory=lambda: "trace-status",
    )

    bridge.start(repo_root=repo_root, goal="检查状态", workspace_mode="readonly", supervised=True)
    snapshot = bridge.status(repo_root=repo_root, bridge_id=None)

    assert snapshot["bridge"]["bridge_id"] == "bridge-status"
    assert snapshot["bridge"]["session_id"] == "session-status"
    assert snapshot["latest_turn"]["turn_id"] == "turn-start"
    assert snapshot["session"]["session_id"] == "session-status"
    assert snapshot["latest_verification"] is None
    assert snapshot["latest_challenge"] is None
    assert snapshot["suggested_next"]["needs_codex_review"] is True
    assert snapshot["suggested_next"]["verification_failed"] is False
    assert snapshot["suggested_next"]["challenge_pending"] is False
```

```python
def test_bridge_verify_records_verification_for_latest_turn(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    verification_runner = FakeBridgeVerificationRunner(recorder, [False])

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        verification_runner=verification_runner,
        bridge_id_factory=lambda: "bridge-verify",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-verify",
        task_id_factory=lambda: "task-verify",
        trace_id_factory=lambda: "trace-verify",
    )

    bridge.start(repo_root=repo_root, goal="运行验证", workspace_mode="readonly", supervised=True)
    result = bridge.verify(repo_root=repo_root, bridge_id=None, command="pytest -q")

    assert result["verification"]["passed"] is False
    assert result["verification"]["turn_id"] == "turn-start"
    assert result["bridge"]["latest_verification_status"] == "failed"
    assert verification_runner.commands == ["pytest -q"]

    details = recorder.read_session("session-verify")
    assert details["verifications"][0]["command"] == "pytest -q"
    assert details["turns"][-1]["phase"] == "final_verify"
```

Add this fake class near the existing fake classes in `tests/test_claude_bridge.py`:

```python
class FakeBridgeVerificationRunner:
    def __init__(self, recorder: SessionRecorder, results: list[bool]):
        self._recorder = recorder
        self._results = list(results)
        self.commands: list[str] = []

    def run(self, session_id: str, turn_id: str, command: str) -> VerificationRecord:
        self.commands.append(command)
        passed = self._results.pop(0)
        record = VerificationRecord(
            verification_id=f"verification-{len(self.commands)}",
            session_id=session_id,
            turn_id=turn_id,
            kind=VerificationKind.COMMAND,
            passed=passed,
            command=command,
            exit_code=0 if passed else 1,
            summary=f"verification {'passed' if passed else 'failed'}",
        )
        self._recorder.append_verification(session_id, record)
        return record
```

Add imports:

```python
from codex_claude_orchestrator.models import VerificationKind, VerificationRecord
```

- [x] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_claude_bridge.py::test_bridge_status_returns_supervision_snapshot \
  tests/test_claude_bridge.py::test_bridge_verify_records_verification_for_latest_turn \
  -v
```

Expected: FAIL with `AttributeError` for missing `status` and `verify`.

- [x] **Step 3: Implement `status`**

Add method to `ClaudeBridge`:

```python
def status(self, *, repo_root: Path, bridge_id: str | None) -> dict[str, Any]:
    self._resolve_repo(repo_root)
    resolved_bridge_id = self._resolve_bridge_id(bridge_id)
    record = self._read_record(resolved_bridge_id)
    turns = self._read_turns(resolved_bridge_id)
    session_payload = None
    latest_verification = None
    latest_challenge = None

    if record.get("session_id"):
        session_payload = self._session_recorder.read_session(str(record["session_id"]))
        verifications = session_payload["verifications"]
        challenges = session_payload["challenges"]
        latest_verification = verifications[-1] if verifications else None
        latest_challenge = challenges[-1] if challenges else None

    return {
        "bridge": record,
        "session": session_payload["session"] if session_payload else None,
        "latest_turn": turns[-1] if turns else None,
        "latest_verification": latest_verification,
        "latest_challenge": latest_challenge,
        "suggested_next": self._suggest_next(record, latest_verification, latest_challenge),
    }
```

Add helper:

```python
def _suggest_next(
    self,
    record: dict[str, Any],
    latest_verification: dict[str, Any] | None,
    latest_challenge: dict[str, Any] | None,
) -> dict[str, bool]:
    verification_failed = bool(latest_verification and not latest_verification.get("passed"))
    challenge_pending = bool(latest_challenge and record.get("latest_challenge_id") == latest_challenge.get("challenge_id"))
    return {
        "needs_codex_review": record.get("status") in ("active", "failed", "needs_human"),
        "verification_failed": verification_failed,
        "challenge_pending": challenge_pending,
    }
```

- [x] **Step 4: Implement `verify`**

Add method:

```python
def verify(
    self,
    *,
    repo_root: Path,
    bridge_id: str | None,
    command: str,
    turn_id: str | None = None,
) -> dict[str, Any]:
    self._resolve_repo(repo_root)
    resolved_bridge_id = self._resolve_bridge_id(bridge_id)
    record = self._read_record(resolved_bridge_id)
    self._require_supervised(record)
    if self._verification_runner is None:
        raise ValueError("supervised bridge verification runner is not configured")

    resolved_turn_id = turn_id or str(record.get("latest_turn_id") or "")
    if not resolved_turn_id:
        raise ValueError(f"bridge {resolved_bridge_id} has no turn to verify")

    verification = self._verification_runner.run(str(record["session_id"]), resolved_turn_id, command)
    self._append_verification_turn(record, verification)
    updated = dict(record)
    updated["latest_verification_status"] = "passed" if verification.passed else "failed"
    updated["updated_at"] = verification.created_at
    self._write_record(resolved_bridge_id, updated)
    self._append_log_verification(resolved_bridge_id, verification)
    return {"bridge": updated, "verification": verification.to_dict()}
```

Add helpers:

```python
def _require_supervised(self, record: dict[str, Any]) -> None:
    if not record.get("supervised") or not record.get("session_id"):
        raise ValueError(f"bridge {record['bridge_id']} is not supervised")
```

```python
def _append_verification_turn(self, record: dict[str, Any], verification: VerificationRecord) -> None:
    turn = TurnRecord(
        turn_id=f"turn-{verification.verification_id}",
        session_id=str(record["session_id"]),
        round_index=int(record.get("turn_count", 0)),
        phase=TurnPhase.FINAL_VERIFY,
        task_id=str(record["root_task_id"]),
        from_agent="codex",
        to_agent="codex",
        message=verification.command or "",
        decision="passed" if verification.passed else "failed",
        summary=verification.summary,
        payload={"verification": verification.to_dict()},
    )
    self._session_recorder.append_turn(str(record["session_id"]), turn)
```

```python
def _append_log_verification(self, bridge_id: str, verification: VerificationRecord) -> None:
    status = "PASS" if verification.passed else "FAIL"
    self._append_log_text(
        bridge_id,
        "\n".join(
            [
                "",
                f"[{verification.created_at}] [VERIFY] {status}",
                verification.command or "",
                verification.summary,
                "",
            ]
        ),
    )
```

- [x] **Step 5: Run the focused tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_claude_bridge.py::test_bridge_status_returns_supervision_snapshot \
  tests/test_claude_bridge.py::test_bridge_verify_records_verification_for_latest_turn \
  -v
```

Expected: PASS.

- [x] **Step 6: Run bridge and verification tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_claude_bridge.py tests/test_verification_runner.py -v
```

Expected: PASS.

- [x] **Step 7: Commit**

Run:

```bash
git add src/codex_claude_orchestrator/claude_bridge.py tests/test_claude_bridge.py
git commit -m "feat: add supervised bridge status and verification"
```

## Task 3: Bridge Challenge and Final Decision Commands

**Files:**
- Modify: `src/codex_claude_orchestrator/claude_bridge.py`
- Modify: `tests/test_claude_bridge.py`

- [x] **Step 1: Write failing challenge and finalizer tests**

Append these tests to `tests/test_claude_bridge.py`:

```python
def test_bridge_challenge_send_records_challenge_and_sends_repair_goal(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")
    responses = [
        CompletedProcess(
            ["claude"],
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"第一轮。"}',
            stderr="",
        ),
        CompletedProcess(
            ["claude"],
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"修复完成。"}',
            stderr="",
        ),
    ]
    commands = []

    def fake_runner(command, **kwargs):
        commands.append(list(command))
        return responses.pop(0)

    turn_ids = iter(["turn-start", "turn-repair"])
    trace_ids = iter(["trace-start", "trace-repair"])
    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=fake_runner,
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-challenge",
        turn_id_factory=lambda: next(turn_ids),
        session_id_factory=lambda: "session-challenge",
        task_id_factory=lambda: "task-challenge",
        trace_id_factory=lambda: next(trace_ids),
        challenge_id_factory=lambda: "challenge-bridge",
    )

    bridge.start(repo_root=repo_root, goal="实现功能", workspace_mode="shared", supervised=True)
    result = bridge.challenge(
        repo_root=repo_root,
        bridge_id=None,
        summary="缺少验证证据",
        repair_goal="补充测试并汇报验证结果",
        send=True,
    )

    assert result["challenge"]["challenge_id"] == "challenge-bridge"
    assert result["bridge"]["latest_challenge_id"] == "challenge-bridge"
    assert result["latest_turn"]["turn_id"] == "turn-repair"
    assert "补充测试并汇报验证结果" in commands[1]
    details = recorder.read_session("session-challenge")
    assert details["challenges"][0]["summary"] == "缺少验证证据"
    assert details["turns"][1]["phase"] == "challenge"
    assert details["turns"][2]["phase"] == "execute"
```

```python
def test_bridge_accept_and_needs_human_finalize_supervised_session(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = SessionRecorder(repo_root / ".orchestrator")

    bridge = ClaudeBridge(
        state_root=repo_root / ".orchestrator",
        runner=lambda command, **kwargs: CompletedProcess(
            command,
            0,
            stdout='{"type":"result","session_id":"claude-session-1","result":"完成。"}',
            stderr="",
        ),
        session_recorder=recorder,
        bridge_id_factory=lambda: "bridge-final",
        turn_id_factory=lambda: "turn-start",
        session_id_factory=lambda: "session-final",
        task_id_factory=lambda: "task-final",
        trace_id_factory=lambda: "trace-final",
    )

    bridge.start(repo_root=repo_root, goal="最终确认", workspace_mode="readonly", supervised=True)
    accepted = bridge.accept(repo_root=repo_root, bridge_id=None, summary="Codex reviewed and accepted")

    assert accepted["bridge"]["status"] == "accepted"
    assert recorder.read_session("session-final")["session"]["status"] == "accepted"

    blocked = bridge.needs_human(repo_root=repo_root, bridge_id=None, summary="Need user decision")

    assert blocked["bridge"]["status"] == "needs_human"
    details = recorder.read_session("session-final")
    assert details["session"]["status"] == "needs_human"
    assert details["final_report"]["final_summary"] == "Need user decision"
```

- [x] **Step 2: Run focused tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_claude_bridge.py::test_bridge_challenge_send_records_challenge_and_sends_repair_goal \
  tests/test_claude_bridge.py::test_bridge_accept_and_needs_human_finalize_supervised_session \
  -v
```

Expected: FAIL with `AttributeError` for missing `challenge`, `accept`, and `needs_human`.

- [x] **Step 3: Implement `challenge`**

Add imports in `claude_bridge.py`:

```python
from codex_claude_orchestrator.models import ChallengeRecord, ChallengeType
```

Add method:

```python
def challenge(
    self,
    *,
    repo_root: Path,
    bridge_id: str | None,
    summary: str,
    repair_goal: str,
    send: bool = False,
) -> dict[str, Any]:
    repo = self._resolve_repo(repo_root)
    resolved_bridge_id = self._resolve_bridge_id(bridge_id)
    record = self._read_record(resolved_bridge_id)
    self._require_supervised(record)
    latest_turn_id = str(record.get("latest_turn_id") or "")
    if not latest_turn_id:
        raise ValueError(f"bridge {resolved_bridge_id} has no turn to challenge")

    challenge = ChallengeRecord(
        challenge_id=self._challenge_id_factory(),
        session_id=str(record["session_id"]),
        turn_id=latest_turn_id,
        round_index=int(record.get("turn_count", 0)),
        challenge_type=ChallengeType.QUALITY_RISK,
        summary=summary,
        question="What repair is needed for Codex to accept this bridge turn?",
        expected_evidence="Claude should provide repaired work and verification evidence.",
        severity=2,
        evidence={"bridge_id": resolved_bridge_id, "turn_id": latest_turn_id},
        repair_goal=repair_goal,
    )
    self._session_recorder.append_challenge(str(record["session_id"]), challenge)
    self._append_challenge_turn(record, challenge)
    updated = dict(record)
    updated["latest_challenge_id"] = challenge.challenge_id
    updated["updated_at"] = challenge.created_at
    self._write_record(resolved_bridge_id, updated)
    self._append_log_challenge(resolved_bridge_id, challenge)

    sent_turn = None
    if send:
        send_result = self.send(
            repo_root=repo,
            bridge_id=resolved_bridge_id,
            message=repair_goal,
        )
        updated = send_result["bridge"]
        sent_turn = send_result["latest_turn"]

    return {"bridge": updated, "challenge": challenge.to_dict(), "latest_turn": sent_turn}
```

Add helpers:

```python
def _append_challenge_turn(self, record: dict[str, Any], challenge: ChallengeRecord) -> None:
    turn = TurnRecord(
        turn_id=f"turn-{challenge.challenge_id}",
        session_id=str(record["session_id"]),
        round_index=challenge.round_index,
        phase=TurnPhase.CHALLENGE,
        task_id=str(record["root_task_id"]),
        from_agent="codex",
        to_agent="claude",
        message=challenge.repair_goal,
        decision="challenge",
        summary=challenge.summary,
        payload={"challenge": challenge.to_dict()},
    )
    self._session_recorder.append_turn(str(record["session_id"]), turn)
```

```python
def _append_log_challenge(self, bridge_id: str, challenge: ChallengeRecord) -> None:
    self._append_log_text(
        bridge_id,
        "\n".join(
            [
                "",
                f"[{challenge.created_at}] [CHALLENGE]",
                challenge.summary,
                "",
                "[REPAIR_GOAL]",
                challenge.repair_goal,
                "",
            ]
        ),
    )
```

- [x] **Step 4: Implement `accept` and `needs_human`**

Add methods:

```python
def accept(self, *, repo_root: Path, bridge_id: str | None, summary: str) -> dict[str, Any]:
    return self._finalize_supervised_bridge(
        repo_root=repo_root,
        bridge_id=bridge_id,
        status=SessionStatus.ACCEPTED,
        bridge_status="accepted",
        summary=summary,
    )
```

```python
def needs_human(self, *, repo_root: Path, bridge_id: str | None, summary: str) -> dict[str, Any]:
    return self._finalize_supervised_bridge(
        repo_root=repo_root,
        bridge_id=bridge_id,
        status=SessionStatus.NEEDS_HUMAN,
        bridge_status="needs_human",
        summary=summary,
    )
```

Add helper:

```python
def _finalize_supervised_bridge(
    self,
    *,
    repo_root: Path,
    bridge_id: str | None,
    status: SessionStatus,
    bridge_status: str,
    summary: str,
) -> dict[str, Any]:
    self._resolve_repo(repo_root)
    resolved_bridge_id = self._resolve_bridge_id(bridge_id)
    record = self._read_record(resolved_bridge_id)
    self._require_supervised(record)
    self._session_recorder.finalize_session(
        str(record["session_id"]),
        status,
        summary,
        current_round=int(record.get("turn_count", 0)),
    )
    updated = dict(record)
    updated["status"] = bridge_status
    updated["updated_at"] = utc_now()
    self._write_record(resolved_bridge_id, updated)
    self._append_log_status(resolved_bridge_id, f"{bridge_status}: {summary}")
    return {"bridge": updated, "session": self._session_recorder.read_session(str(record["session_id"]))["session"]}
```

- [x] **Step 5: Run focused tests to verify they pass**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_claude_bridge.py::test_bridge_challenge_send_records_challenge_and_sends_repair_goal \
  tests/test_claude_bridge.py::test_bridge_accept_and_needs_human_finalize_supervised_session \
  -v
```

Expected: PASS.

- [x] **Step 6: Run all bridge tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_claude_bridge.py -v
```

Expected: PASS.

- [x] **Step 7: Commit**

Run:

```bash
git add src/codex_claude_orchestrator/claude_bridge.py tests/test_claude_bridge.py
git commit -m "feat: add supervised bridge challenges"
```

## Task 4: CLI Wiring for Supervised Bridge Commands

**Files:**
- Modify: `src/codex_claude_orchestrator/cli.py`
- Modify: `tests/test_cli.py`

- [x] **Step 1: Write failing CLI routing test**

Replace `FakeClaudeBridge` in `tests/test_cli.py` with methods for the new commands:

```python
class FakeClaudeBridge:
    def __init__(self):
        self.calls = []

    def start(self, **kwargs):
        self.calls.append({"method": "start", **kwargs})
        return {"bridge": {"bridge_id": "bridge-cli"}, "latest_turn": {"turn_id": "turn-cli"}}

    def send(self, **kwargs):
        self.calls.append({"method": "send", **kwargs})
        return {
            "bridge": {"bridge_id": "bridge-cli"},
            "latest_turn": {"turn_id": "turn-cli", "message": kwargs["message"]},
        }

    def tail(self, **kwargs):
        self.calls.append({"method": "tail", **kwargs})
        return {"bridge": {"bridge_id": "bridge-cli"}, "turns": [{"turn_id": "turn-cli"}]}

    def list(self, **kwargs):
        self.calls.append({"method": "list", **kwargs})
        return [{"bridge_id": "bridge-cli"}]

    def status(self, **kwargs):
        self.calls.append({"method": "status", **kwargs})
        return {"bridge": {"bridge_id": "bridge-cli"}, "suggested_next": {"needs_codex_review": True}}

    def verify(self, **kwargs):
        self.calls.append({"method": "verify", **kwargs})
        return {"bridge": {"bridge_id": "bridge-cli"}, "verification": {"passed": True}}

    def challenge(self, **kwargs):
        self.calls.append({"method": "challenge", **kwargs})
        return {"bridge": {"bridge_id": "bridge-cli"}, "challenge": {"challenge_id": "challenge-cli"}}

    def accept(self, **kwargs):
        self.calls.append({"method": "accept", **kwargs})
        return {"bridge": {"bridge_id": "bridge-cli", "status": "accepted"}}

    def needs_human(self, **kwargs):
        self.calls.append({"method": "needs_human", **kwargs})
        return {"bridge": {"bridge_id": "bridge-cli", "status": "needs_human"}}
```

Extend `test_claude_bridge_commands_route_to_bridge` after the existing list command:

```python
    stdout = StringIO()
    with redirect_stdout(stdout):
        status_exit = main(["claude", "bridge", "status", "--repo", str(repo_root)])
    status_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        verify_exit = main(
            [
                "claude",
                "bridge",
                "verify",
                "--repo",
                str(repo_root),
                "--command",
                "pytest -q",
            ]
        )
    verify_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        challenge_exit = main(
            [
                "claude",
                "bridge",
                "challenge",
                "--repo",
                str(repo_root),
                "--summary",
                "missing verification",
                "--repair-goal",
                "run pytest",
                "--send",
            ]
        )
    challenge_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        accept_exit = main(
            [
                "claude",
                "bridge",
                "accept",
                "--repo",
                str(repo_root),
                "--summary",
                "accepted",
            ]
        )
    accept_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        needs_human_exit = main(
            [
                "claude",
                "bridge",
                "needs-human",
                "--repo",
                str(repo_root),
                "--summary",
                "need input",
            ]
        )
    needs_human_payload = json.loads(stdout.getvalue())

    assert status_exit == 0
    assert verify_exit == 0
    assert challenge_exit == 0
    assert accept_exit == 0
    assert needs_human_exit == 0
    assert status_payload["suggested_next"]["needs_codex_review"] is True
    assert verify_payload["verification"]["passed"] is True
    assert challenge_payload["challenge"]["challenge_id"] == "challenge-cli"
    assert accept_payload["bridge"]["status"] == "accepted"
    assert needs_human_payload["bridge"]["status"] == "needs_human"
```

Update the expected `fake_bridge.calls` list to include these entries:

```python
        {
            "method": "start",
            "repo_root": repo_root.resolve(),
            "goal": "Inspect repo",
            "workspace_mode": "readonly",
            "visual": "log",
            "dry_run": False,
            "supervised": True,
        },
        {
            "method": "send",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "message": "继续",
            "dry_run": False,
        },
        {
            "method": "tail",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "limit": 1,
        },
        {"method": "list", "repo_root": repo_root.resolve()},
        {"method": "status", "repo_root": repo_root.resolve(), "bridge_id": None},
        {
            "method": "verify",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "command": "pytest -q",
            "turn_id": None,
        },
        {
            "method": "challenge",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "summary": "missing verification",
            "repair_goal": "run pytest",
            "send": True,
        },
        {
            "method": "accept",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "summary": "accepted",
        },
        {
            "method": "needs_human",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "summary": "need input",
        },
```

- [x] **Step 2: Write failing CLI parser assertion for `--supervised`**

In the start section of `test_claude_bridge_commands_route_to_bridge`, add `--supervised` after `--visual log`. The expected start call already contains `"supervised": True`.

- [x] **Step 3: Run the CLI test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_claude_bridge_commands_route_to_bridge -v
```

Expected: FAIL because the parser does not expose the new commands and `--supervised`.

- [x] **Step 4: Wire parser arguments**

In `build_parser`, add to `claude_bridge_start`:

```python
claude_bridge_start.add_argument("--supervised", action="store_true")
```

Add subparsers:

```python
    claude_bridge_status = claude_bridge_subparsers.add_parser("status", help="Show supervised bridge status")
    claude_bridge_status.add_argument("--repo", required=True)
    claude_bridge_status.add_argument("--bridge-id", required=False)

    claude_bridge_verify = claude_bridge_subparsers.add_parser("verify", help="Run supervised bridge verification")
    claude_bridge_verify.add_argument("--repo", required=True)
    claude_bridge_verify.add_argument("--bridge-id", required=False)
    claude_bridge_verify.add_argument("--turn-id", required=False)
    claude_bridge_verify.add_argument("--command", required=True)

    claude_bridge_challenge = claude_bridge_subparsers.add_parser("challenge", help="Record a Codex bridge challenge")
    claude_bridge_challenge.add_argument("--repo", required=True)
    claude_bridge_challenge.add_argument("--bridge-id", required=False)
    claude_bridge_challenge.add_argument("--summary", required=True)
    claude_bridge_challenge.add_argument("--repair-goal", required=True)
    claude_bridge_challenge.add_argument("--send", action="store_true")

    claude_bridge_accept = claude_bridge_subparsers.add_parser("accept", help="Accept a supervised bridge")
    claude_bridge_accept.add_argument("--repo", required=True)
    claude_bridge_accept.add_argument("--bridge-id", required=False)
    claude_bridge_accept.add_argument("--summary", required=True)

    claude_bridge_needs_human = claude_bridge_subparsers.add_parser(
        "needs-human",
        help="Mark a supervised bridge as needing human review",
    )
    claude_bridge_needs_human.add_argument("--repo", required=True)
    claude_bridge_needs_human.add_argument("--bridge-id", required=False)
    claude_bridge_needs_human.add_argument("--summary", required=True)
```

- [x] **Step 5: Wire CLI command handling**

In the `bridge.start(...)` call, pass:

```python
supervised=args.supervised,
```

Add command branches:

```python
            if args.claude_bridge_command == "status":
                print(
                    json.dumps(
                        bridge.status(
                            repo_root=repo_root,
                            bridge_id=args.bridge_id,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "verify":
                print(
                    json.dumps(
                        bridge.verify(
                            repo_root=repo_root,
                            bridge_id=args.bridge_id,
                            command=args.command,
                            turn_id=args.turn_id,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "challenge":
                print(
                    json.dumps(
                        bridge.challenge(
                            repo_root=repo_root,
                            bridge_id=args.bridge_id,
                            summary=args.summary,
                            repair_goal=args.repair_goal,
                            send=args.send,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "accept":
                print(
                    json.dumps(
                        bridge.accept(
                            repo_root=repo_root,
                            bridge_id=args.bridge_id,
                            summary=args.summary,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "needs-human":
                print(
                    json.dumps(
                        bridge.needs_human(
                            repo_root=repo_root,
                            bridge_id=args.bridge_id,
                            summary=args.summary,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
```

- [x] **Step 6: Build supervised bridge dependencies in `build_claude_bridge`**

Replace `build_claude_bridge` with:

```python
def build_claude_bridge(repo_root: Path) -> ClaudeBridge:
    state_root = repo_root / ".orchestrator"
    session_recorder = SessionRecorder(state_root)
    return ClaudeBridge(
        state_root,
        session_recorder=session_recorder,
        verification_runner=VerificationRunner(
            repo_root=repo_root,
            session_recorder=session_recorder,
            policy_gate=PolicyGate(),
        ),
        result_evaluator=ResultEvaluator(),
    )
```

- [x] **Step 7: Run CLI test to verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py::test_claude_bridge_commands_route_to_bridge -v
```

Expected: PASS.

- [x] **Step 8: Run CLI and bridge tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py tests/test_claude_bridge.py -v
```

Expected: PASS.

- [x] **Step 9: Commit**

Run:

```bash
git add src/codex_claude_orchestrator/cli.py tests/test_cli.py
git commit -m "feat: wire supervised bridge cli"
```

## Task 5: End-to-end Verification and Documentation Check

**Files:**
- Modify: `docs/superpowers/plans/2026-04-29-codex-in-loop-claude-bridge-supervision.md`

- [x] **Step 1: Run targeted test suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_claude_bridge.py tests/test_cli.py tests/test_session_recorder.py tests/test_verification_runner.py -v
```

Expected: PASS.

- [x] **Step 2: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest -v
```

Expected: PASS.

- [x] **Step 3: Smoke-check parser help**

Run:

```bash
.venv/bin/orchestrator claude bridge start --help
.venv/bin/orchestrator claude bridge status --help
.venv/bin/orchestrator claude bridge verify --help
.venv/bin/orchestrator claude bridge challenge --help
.venv/bin/orchestrator claude bridge accept --help
.venv/bin/orchestrator claude bridge needs-human --help
```

Expected: each command exits 0 and prints its usage text.

- [x] **Step 4: Mark implementation plan tasks complete as they are executed**

Edit this plan so each completed checkbox reflects the actual implementation state. Keep skipped steps unchecked with a short note in the final response.

- [x] **Step 5: Commit plan checkbox updates**

Run:

```bash
git add docs/superpowers/plans/2026-04-29-codex-in-loop-claude-bridge-supervision.md
git commit -m "docs: update supervised bridge plan"
```
