# V4 Event-Native Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first V4 event-native foundation slice: PostgreSQL event store protocol, canonical artifact paths, outbox/read acknowledgement models, watcher evidence events, and completion decisions that do not let watchers finalize turns.

**Architecture:** Keep V4 state decisions event-driven. Runtime watchers emit raw evidence only; `CompletionDetector` owns terminal turn decisions. Local artifacts live under `<repo_root>/.orchestrator/crews/<crew_id>/artifacts/v4`; production events use a `PostgresEventStore` behind a protocol, while tests can keep using local stores through the same interface.

**Tech Stack:** Python 3.11+, pytest, optional `psycopg` for PostgreSQL, existing `src/codex_claude_orchestrator/v4` package.

---

## File Structure

- Create `src/codex_claude_orchestrator/v4/event_store_protocol.py`
  Defines the minimal `EventStore` protocol shared by SQLite test store and PostgreSQL production store.
- Create `src/codex_claude_orchestrator/v4/postgres_event_store.py`
  Implements `PostgresEventStore`, schema migration SQL, env-based config, idempotent append, and clear missing-driver/password errors.
- Modify `src/codex_claude_orchestrator/v4/events.py`
  Add `round_id` and `contract_id` to `AgentEvent` normalization.
- Modify `src/codex_claude_orchestrator/v4/event_store.py`
  Make `SQLiteEventStore` satisfy the new protocol and preserve old tests while supporting optional `round_id` and `contract_id`.
- Create `src/codex_claude_orchestrator/v4/paths.py`
  Owns canonical V4 artifact root resolution.
- Create `src/codex_claude_orchestrator/v4/outbox.py`
  Defines worker outbox result parsing and validation, including acknowledged message ids.
- Create `src/codex_claude_orchestrator/v4/turn_context.py`
  Builds worker turn context with unread inbox digest without advancing read cursors.
- Create `src/codex_claude_orchestrator/v4/watchers.py`
  Defines evidence watcher helpers/events for transcript/output/outbox/marker/process/deadline evidence.
- Modify `src/codex_claude_orchestrator/v4/completion.py`
  Make source-write marker-only completion inconclusive unless a valid outbox exists.
- Modify `src/codex_claude_orchestrator/v4/runtime.py`
  Add `completion_mode`, `contract_id`, and source-write/default structured result requirement fields to `TurnEnvelope`.
- Modify `src/codex_claude_orchestrator/v4/supervisor.py`
  Pass new turn fields through and keep terminal turn-state writes inside completion handling.
- Test files:
  - Create `tests/v4/test_postgres_event_store.py`
  - Create `tests/v4/test_paths.py`
  - Create `tests/v4/test_outbox.py`
  - Create `tests/v4/test_turn_context.py`
  - Create `tests/v4/test_watchers.py`
  - Modify `tests/v4/test_event_store.py`
  - Modify `tests/v4/test_runtime_models.py`
  - Modify `tests/v4/test_completion.py`
  - Modify `tests/v4/test_supervisor.py`

## Task 1: Event Store Protocol and PostgreSQL Store

**Files:**
- Create: `src/codex_claude_orchestrator/v4/event_store_protocol.py`
- Create: `src/codex_claude_orchestrator/v4/postgres_event_store.py`
- Modify: `src/codex_claude_orchestrator/v4/events.py`
- Modify: `src/codex_claude_orchestrator/v4/event_store.py`
- Test: `tests/v4/test_postgres_event_store.py`
- Test: `tests/v4/test_event_store.py`

- [ ] **Step 1: Write failing tests for event metadata and PostgreSQL config**

Add tests that show events carry `round_id` and `contract_id`, PostgreSQL config has safe non-secret defaults, and missing password fails before connection.

```python
def test_sqlite_event_store_round_and_contract_fields(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")

    event = store.append(
        stream_id="crew-1",
        type="turn.requested",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        contract_id="contract-1",
        idempotency_key="crew-1/turn-1/requested",
    )

    loaded = store.list_by_turn("turn-1")[0]
    assert event.round_id == "round-1"
    assert loaded.contract_id == "contract-1"


def test_postgres_config_uses_safe_defaults_without_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_PASSWORD", raising=False)

    config = PostgresEventStoreConfig.from_env()

    assert config.host == "124.222.58.173"
    assert config.database == "ragbase"
    assert config.user == "ragbase"
    assert config.port == 5432
    assert config.password is None


def test_postgres_store_requires_password_before_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_PASSWORD", raising=False)
    store = PostgresEventStore(PostgresEventStoreConfig.from_env())

    with pytest.raises(PostgresConfigurationError, match="PG_PASSWORD"):
        store.initialize()
```

- [ ] **Step 2: Run tests and verify red**

Run: `/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_event_store.py tests/v4/test_postgres_event_store.py -q`

Expected: FAIL because `round_id`, `contract_id`, and PostgreSQL classes do not exist yet.

- [ ] **Step 3: Implement protocol, event fields, SQLite compatibility, and PostgreSQL skeleton**

Implementation details:

```python
@dataclass(frozen=True, slots=True)
class PostgresEventStoreConfig:
    host: str = "124.222.58.173"
    database: str = "ragbase"
    user: str = "ragbase"
    port: int = 5432
    password: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "PostgresEventStoreConfig":
        env = environ or os.environ
        return cls(
            host=env.get("PG_HOST", cls.host),
            database=env.get("PG_DB", cls.database),
            user=env.get("PG_USER", cls.user),
            port=int(env.get("PG_PORT", str(cls.port))),
            password=env.get("PG_PASSWORD") or None,
        )
```

`PostgresEventStore.initialize()` must call `config.require_password()` before importing or connecting with `psycopg`. The implementation may lazy-import `psycopg` so normal tests do not require installing it.

- [ ] **Step 4: Run focused tests and verify green**

Run: `/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_event_store.py tests/v4/test_postgres_event_store.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/event_store_protocol.py \
        src/codex_claude_orchestrator/v4/postgres_event_store.py \
        src/codex_claude_orchestrator/v4/events.py \
        src/codex_claude_orchestrator/v4/event_store.py \
        tests/v4/test_postgres_event_store.py \
        tests/v4/test_event_store.py
git commit -m "feat: add v4 postgres event store protocol"
```

## Task 2: Canonical V4 Artifact Root

**Files:**
- Create: `src/codex_claude_orchestrator/v4/paths.py`
- Test: `tests/v4/test_paths.py`

- [ ] **Step 1: Write failing path resolver tests**

```python
def test_v4_paths_use_canonical_artifact_root(tmp_path: Path) -> None:
    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")

    assert paths.state_root == tmp_path / ".orchestrator"
    assert paths.crew_root == tmp_path / ".orchestrator" / "crews" / "crew-1"
    assert paths.artifact_root == tmp_path / ".orchestrator" / "crews" / "crew-1" / "artifacts" / "v4"
    assert paths.worker_root("worker-1") == paths.artifact_root / "workers" / "worker-1"
    assert paths.outbox_path("worker-1", "turn-1") == paths.artifact_root / "workers" / "worker-1" / "outbox" / "turn-1.json"


def test_v4_paths_reject_unsafe_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        V4Paths(repo_root=tmp_path, crew_id="../crew")

    paths = V4Paths(repo_root=tmp_path, crew_id="crew-1")
    with pytest.raises(ValueError, match="unsafe"):
        paths.outbox_path("worker-1", "../turn")
```

- [ ] **Step 2: Run test and verify red**

Run: `/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_paths.py -q`

Expected: FAIL because `V4Paths` does not exist.

- [ ] **Step 3: Implement minimal path resolver**

Implement immutable `V4Paths` with properties for state root, crew root, artifact root, worker root, inbox/outbox/patch/change paths, and a `_safe_id()` helper that rejects absolute paths, `..`, `/`, and `:`.

- [ ] **Step 4: Run focused tests and verify green**

Run: `/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_paths.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/paths.py tests/v4/test_paths.py
git commit -m "feat: add v4 canonical artifact paths"
```

## Task 3: Outbox Result and Turn Context

**Files:**
- Create: `src/codex_claude_orchestrator/v4/outbox.py`
- Create: `src/codex_claude_orchestrator/v4/turn_context.py`
- Test: `tests/v4/test_outbox.py`
- Test: `tests/v4/test_turn_context.py`

- [ ] **Step 1: Write failing outbox and turn context tests**

```python
def test_outbox_result_parses_acknowledged_message_ids() -> None:
    result = WorkerOutboxResult.from_dict(
        {
            "crew_id": "crew-1",
            "worker_id": "worker-1",
            "turn_id": "turn-1",
            "status": "completed",
            "summary": "done",
            "changed_files": ["src/app.py"],
            "acknowledged_message_ids": ["msg-1"],
        }
    )

    assert result.is_valid
    assert result.acknowledged_message_ids == ["msg-1"]


def test_turn_context_builds_unread_digest_without_marking_read(tmp_path: Path) -> None:
    recorder = CrewRecorder(tmp_path / ".orchestrator")
    recorder.start_crew(CrewRecord(crew_id="crew-1", repo=str(tmp_path), root_goal="goal"))
    bus = AgentMessageBus(recorder)
    bus.send(
        crew_id="crew-1",
        sender="codex",
        recipient="worker-1",
        message_type=AgentMessageType.REQUEST,
        body="review this",
    )

    context = TurnContextBuilder(bus).build(crew_id="crew-1", worker_id="worker-1")

    assert context.unread_count == 1
    assert "review this" in context.unread_inbox_digest
    assert bus.cursor_summary("crew-1") == {}
```

- [ ] **Step 2: Run tests and verify red**

Run: `/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_outbox.py tests/v4/test_turn_context.py -q`

Expected: FAIL because modules do not exist.

- [ ] **Step 3: Implement outbox and context models**

`WorkerOutboxResult.from_dict()` must validate required ids, status in `completed|blocked|failed|inconclusive`, list fields, and expose `is_valid`.

`TurnContextBuilder.build()` must call `AgentMessageBus.read_inbox(mark_read=False)`, summarize unread messages, and never update cursors.

- [ ] **Step 4: Run focused tests and verify green**

Run: `/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_outbox.py tests/v4/test_turn_context.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/outbox.py \
        src/codex_claude_orchestrator/v4/turn_context.py \
        tests/v4/test_outbox.py \
        tests/v4/test_turn_context.py
git commit -m "feat: add v4 outbox and turn context"
```

## Task 4: Watcher Evidence and Completion Boundaries

**Files:**
- Create: `src/codex_claude_orchestrator/v4/watchers.py`
- Modify: `src/codex_claude_orchestrator/v4/runtime.py`
- Modify: `src/codex_claude_orchestrator/v4/completion.py`
- Test: `tests/v4/test_watchers.py`
- Test: `tests/v4/test_runtime_models.py`
- Test: `tests/v4/test_completion.py`

- [ ] **Step 1: Write failing watcher/completion tests**

```python
def test_outbox_watcher_emits_evidence_not_terminal_turn_event(tmp_path: Path) -> None:
    outbox = tmp_path / "turn-1.json"
    outbox.write_text(json.dumps({"crew_id": "crew-1", "worker_id": "worker-1", "turn_id": "turn-1", "status": "completed"}), encoding="utf-8")

    events = list(OutboxWatcher().watch(turn_id="turn-1", worker_id="worker-1", outbox_path=outbox))

    assert [event.type for event in events] == ["worker.outbox.detected"]


def test_source_write_marker_without_outbox_is_inconclusive() -> None:
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="work",
        expected_marker="DONE",
        contract_id="contract-1",
        requires_structured_result=True,
    )
    decision = CompletionDetector.evaluate(
        turn,
        [RuntimeEvent(type="marker.detected", turn_id="turn-1", worker_id="worker-1", payload={"marker": "DONE"})],
    )

    assert decision.event_type == "turn.inconclusive"
    assert decision.reason == "missing_outbox"
```

- [ ] **Step 2: Run tests and verify red**

Run: `/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_watchers.py tests/v4/test_completion.py tests/v4/test_runtime_models.py -q`

Expected: FAIL because watcher and new turn fields do not exist and completion still allows marker-only completion.

- [ ] **Step 3: Implement watcher evidence and completion changes**

`OutboxWatcher` emits only `RuntimeEvent(type="worker.outbox.detected", turn_id=turn_id, worker_id=worker_id, payload={"valid": result.is_valid})`.

`TurnEnvelope` gains:

```python
contract_id: str = ""
completion_mode: str = "structured_required"
requires_structured_result: bool = True
```

`CompletionDetector.evaluate()` treats valid outbox evidence as completion, marker-only source-write turns as `turn.inconclusive/missing_outbox`, and marker-only legacy/read-only turns as complete only when `requires_structured_result` is `False` or `completion_mode == "marker_allowed"`.

- [ ] **Step 4: Run focused tests and verify green**

Run: `/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_watchers.py tests/v4/test_completion.py tests/v4/test_runtime_models.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/watchers.py \
        src/codex_claude_orchestrator/v4/runtime.py \
        src/codex_claude_orchestrator/v4/completion.py \
        tests/v4/test_watchers.py \
        tests/v4/test_runtime_models.py \
        tests/v4/test_completion.py
git commit -m "feat: separate v4 watcher evidence from completion"
```

## Task 5: Supervisor Compatibility and Full Verification

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/supervisor.py`
- Test: `tests/v4/test_supervisor.py`

- [ ] **Step 1: Write failing supervisor regression test**

```python
def test_v4_supervisor_keeps_marker_only_source_turn_waiting(tmp_path: Path) -> None:
    adapter = FakeRuntimeAdapter([RuntimeEvent(type="marker.detected", turn_id="round-1-worker-1-source", worker_id="worker-1", payload={"marker": "DONE"})])
    supervisor = V4Supervisor(
        event_store=SQLiteEventStore(tmp_path / "events.sqlite3"),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=adapter,
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="goal",
        worker_id="worker-1",
        round_id="round-1",
        message="work",
        expected_marker="DONE",
    )

    assert result["status"] == "waiting"
    assert result["reason"] == "missing_outbox"
```

- [ ] **Step 2: Run test and verify red**

Run: `/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4/test_supervisor.py::test_v4_supervisor_keeps_marker_only_source_turn_waiting -q`

Expected: FAIL until supervisor/turn construction passes structured result requirements through completion.

- [ ] **Step 3: Update supervisor turn construction and terminal event append**

Set `contract_id="source_write"` or a caller-provided contract id when creating source turns. Keep terminal event append centralized after `CompletionDetector.evaluate()`.

- [ ] **Step 4: Run V4 tests**

Run: `/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest tests/v4 -q`

Expected: PASS.

- [ ] **Step 5: Run full suite**

Run: `/Users/zhanghaoqian/Documents/zhangzhang/agent/channel/.venv/bin/python -m pytest -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codex_claude_orchestrator/v4/supervisor.py tests/v4/test_supervisor.py
git commit -m "feat: enforce structured completion in v4 supervisor"
```

## Self-Review

Spec coverage:

- PostgreSQL event store and schema versioning: Task 1.
- Canonical local artifact root: Task 2.
- Message cursor/read acknowledgement foundation: Task 3.
- Watchers as evidence-only producers: Task 4.
- Marker-only source-write completion blocked: Tasks 4 and 5.
- Full merge transaction, dirty-base accept protection, CLI main-path migration, and planner upgrade are intentionally deferred to follow-up plans because they are independent subsystems and should each remain independently testable.

Placeholder scan:

- No placeholder tasks are present.

Type consistency:

- `round_id`, `contract_id`, `completion_mode`, `requires_structured_result`, and `acknowledged_message_ids` are named consistently across tasks.
