# Codex Managed Claude Crew V4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build V4 as a durable event-sourced agent runtime/control plane that replaces terminal polling as the source of truth while preserving current Claude Code/tmux workers as an adapter.

**Architecture:** Add a new `codex_claude_orchestrator.v4` package with event store, artifact store, runtime adapter interface, turn lifecycle, completion detector, projections, reconciler, workflow engine, and V4 supervisor facade. Existing V3 `crew` modules remain intact while V4 is introduced behind explicit CLI/UI entry points.

**Tech Stack:** Python dataclasses, stdlib `sqlite3`, JSON artifacts, existing `NativeClaudeSession`, existing crew gates (`WriteScopeGate`, `ReviewVerdictParser`, `CrewReadinessEvaluator`), pytest.

---

## File Structure

Create this package:

```text
src/codex_claude_orchestrator/v4/
  __init__.py
  events.py
  event_store.py
  artifacts.py
  runtime.py
  turns.py
  completion.py
  ingest.py
  projections.py
  reconciler.py
  workflow.py
  supervisor.py
  gates.py
  adapters/
    __init__.py
    tmux_claude.py
    verification.py
```

Create tests:

```text
tests/v4/
  __init__.py
  test_event_store.py
  test_artifacts.py
  test_runtime_models.py
  test_turns.py
  test_completion.py
  test_ingest.py
  test_projections.py
  test_reconciler.py
  test_workflow.py
  test_supervisor.py
  test_tmux_claude_adapter.py
  test_verification_adapter.py
```

Modify existing files only in the integration tasks:

```text
src/codex_claude_orchestrator/cli.py
src/codex_claude_orchestrator/ui/server.py
tests/cli/test_cli.py
tests/ui/test_server.py
```

Do not modify V3 behavior unless a task explicitly says to add a V4 entry point. Existing dynamic crew tests must keep passing.

## Scope Check

V4 spans multiple runtime subsystems, but they are one coherent version boundary: event store, artifact store, runtime adapter, workflow state, projections, recovery, and operator inspection all serve the same durable control plane. The tasks below are ordered so each module is independently testable before it is integrated into the supervisor facade.

## Task 1: V4 Event Model

**Files:**
- Create: `src/codex_claude_orchestrator/v4/__init__.py`
- Create: `src/codex_claude_orchestrator/v4/events.py`
- Create: `tests/v4/__init__.py`
- Create: `tests/v4/test_event_store.py`

- [ ] **Step 1: Write failing tests for event normalization and required fields**

Create empty package marker `tests/v4/__init__.py`.

Create `tests/v4/test_event_store.py` with:

```python
from codex_claude_orchestrator.v4.events import AgentEvent


def test_agent_event_to_dict_normalizes_payload_and_refs():
    event = AgentEvent(
        event_id="evt-1",
        stream_id="crew-1",
        sequence=7,
        type="turn.completed",
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        idempotency_key="crew-1/turn-1/completed",
        payload={"status": "completed"},
        artifact_refs=["turns/turn-1/transcript.txt"],
        created_at="2026-05-01T00:00:00Z",
    )

    assert event.to_dict() == {
        "event_id": "evt-1",
        "stream_id": "crew-1",
        "sequence": 7,
        "type": "turn.completed",
        "crew_id": "crew-1",
        "worker_id": "worker-1",
        "turn_id": "turn-1",
        "idempotency_key": "crew-1/turn-1/completed",
        "payload": {"status": "completed"},
        "artifact_refs": ["turns/turn-1/transcript.txt"],
        "created_at": "2026-05-01T00:00:00Z",
    }


def test_agent_event_rejects_missing_type():
    try:
        AgentEvent(event_id="evt-1", stream_id="crew-1", sequence=1, type="")
    except ValueError as exc:
        assert "type is required" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_event_store.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4'`.

- [ ] **Step 3: Implement event dataclass**

Create `src/codex_claude_orchestrator/v4/__init__.py`:

```python
"""Durable V4 runtime primitives."""
```

Create `src/codex_claude_orchestrator/v4/events.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


def normalize(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {item.name: normalize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {str(key): normalize(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [normalize(inner) for inner in value]
    return value


@dataclass(slots=True)
class AgentEvent:
    event_id: str
    stream_id: str
    sequence: int
    type: str
    crew_id: str = ""
    worker_id: str = ""
    turn_id: str = ""
    idempotency_key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    artifact_refs: list[str] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id is required")
        if not self.stream_id:
            raise ValueError("stream_id is required")
        if not self.type:
            raise ValueError("type is required")
        if self.sequence < 1:
            raise ValueError("sequence must be positive")

    def to_dict(self) -> dict[str, Any]:
        return normalize(self)
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_event_store.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/__init__.py src/codex_claude_orchestrator/v4/events.py tests/v4/__init__.py tests/v4/test_event_store.py
git commit -m "feat: add v4 agent event model"
```

## Task 2: SQLite Event Store

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/event_store.py`
- Modify: `tests/v4/test_event_store.py`

**Execution correction:** `list_by_turn(turn_id)` must return events in append chronology across streams, not lexicographic `stream_id, sequence` order. V4 uses per-stream `sequence` for `list_stream()` and global SQLite append order for turn lifecycle reconstruction. `append()` must also use one write transaction (`BEGIN IMMEDIATE`) for idempotency lookup, sequence allocation, and insert, with explicit connection closing.

- [ ] **Step 1: Add failing event store tests**

Append to `tests/v4/test_event_store.py`:

```python
from pathlib import Path

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore


def test_event_store_appends_sequences_per_stream(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")

    first = store.append(
        stream_id="crew-1",
        type="turn.requested",
        crew_id="crew-1",
        turn_id="turn-1",
        idempotency_key="crew-1/turn-1/requested",
        payload={"phase": "source"},
    )
    second = store.append(
        stream_id="crew-1",
        type="turn.delivered",
        crew_id="crew-1",
        turn_id="turn-1",
        idempotency_key="crew-1/turn-1/delivered",
    )

    assert first.sequence == 1
    assert second.sequence == 2
    assert [event.type for event in store.list_stream("crew-1")] == ["turn.requested", "turn.delivered"]


def test_event_store_dedupes_idempotency_key(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")

    first = store.append(
        stream_id="crew-1",
        type="turn.requested",
        idempotency_key="same-key",
        payload={"attempt": 1},
    )
    duplicate = store.append(
        stream_id="crew-1",
        type="turn.requested",
        idempotency_key="same-key",
        payload={"attempt": 2},
    )

    assert duplicate.event_id == first.event_id
    assert duplicate.payload == {"attempt": 1}
    assert len(store.list_stream("crew-1")) == 1


def test_event_store_lists_events_after_sequence(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(stream_id="crew-1", type="crew.started")
    second = store.append(stream_id="crew-1", type="worker.spawned")

    assert [event.type for event in store.list_stream("crew-1", after_sequence=1)] == [second.type]


def test_event_store_list_by_turn_preserves_append_order_across_streams(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    first = store.append(stream_id="z-stream", type="turn.requested", turn_id="turn-1")
    second = store.append(stream_id="a-stream", type="turn.delivered", turn_id="turn-1")

    assert [event.event_id for event in store.list_by_turn("turn-1")] == [first.event_id, second.event_id]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_event_store.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.event_store'`.

- [ ] **Step 3: Implement SQLite event store**

Create `src/codex_claude_orchestrator/v4/event_store.py`:

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.v4.events import AgentEvent


class SQLiteEventStore:
    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def append(
        self,
        *,
        stream_id: str,
        type: str,
        crew_id: str = "",
        worker_id: str = "",
        turn_id: str = "",
        idempotency_key: str = "",
        payload: dict | None = None,
        artifact_refs: list[str] | None = None,
        created_at: str = "",
    ) -> AgentEvent:
        if idempotency_key:
            existing = self.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return existing
        with self._connect() as db:
            sequence = self._next_sequence(db, stream_id)
            event = AgentEvent(
                event_id=f"evt-{uuid4().hex}",
                stream_id=stream_id,
                sequence=sequence,
                type=type,
                crew_id=crew_id,
                worker_id=worker_id,
                turn_id=turn_id,
                idempotency_key=idempotency_key,
                payload=payload or {},
                artifact_refs=artifact_refs or [],
                created_at=created_at,
            )
            db.execute(
                """
                insert into events (
                    event_id, stream_id, sequence, type, crew_id, worker_id, turn_id,
                    idempotency_key, payload_json, artifact_refs_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.stream_id,
                    event.sequence,
                    event.type,
                    event.crew_id,
                    event.worker_id,
                    event.turn_id,
                    event.idempotency_key,
                    json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(event.artifact_refs, ensure_ascii=False),
                    event.created_at,
                ),
            )
            return event

    def list_stream(self, stream_id: str, *, after_sequence: int = 0) -> list[AgentEvent]:
        with self._connect() as db:
            rows = db.execute(
                """
                select * from events
                where stream_id = ? and sequence > ?
                order by sequence asc
                """,
                (stream_id, after_sequence),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_by_turn(self, turn_id: str) -> list[AgentEvent]:
        with self._connect() as db:
            rows = db.execute(
                "select * from events where turn_id = ? order by rowid asc",
                (turn_id,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def get_by_idempotency_key(self, idempotency_key: str) -> AgentEvent | None:
        with self._connect() as db:
            row = db.execute(
                "select * from events where idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._row_to_event(row) if row else None

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                create table if not exists events (
                    event_id text primary key,
                    stream_id text not null,
                    sequence integer not null,
                    type text not null,
                    crew_id text not null,
                    worker_id text not null,
                    turn_id text not null,
                    idempotency_key text not null,
                    payload_json text not null,
                    artifact_refs_json text not null,
                    created_at text not null
                )
                """
            )
            db.execute("create unique index if not exists idx_events_stream_sequence on events(stream_id, sequence)")
            db.execute(
                """
                create unique index if not exists idx_events_idempotency
                on events(idempotency_key)
                where idempotency_key != ''
                """
            )
            db.execute("create index if not exists idx_events_turn on events(turn_id)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path)
        db.row_factory = sqlite3.Row
        return db

    def _next_sequence(self, db: sqlite3.Connection, stream_id: str) -> int:
        row = db.execute("select coalesce(max(sequence), 0) + 1 as next from events where stream_id = ?", (stream_id,)).fetchone()
        return int(row["next"])

    def _row_to_event(self, row: sqlite3.Row) -> AgentEvent:
        return AgentEvent(
            event_id=row["event_id"],
            stream_id=row["stream_id"],
            sequence=int(row["sequence"]),
            type=row["type"],
            crew_id=row["crew_id"],
            worker_id=row["worker_id"],
            turn_id=row["turn_id"],
            idempotency_key=row["idempotency_key"],
            payload=json.loads(row["payload_json"]),
            artifact_refs=json.loads(row["artifact_refs_json"]),
            created_at=row["created_at"],
        )
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_event_store.py -q
```

Expected: all event store tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/event_store.py tests/v4/test_event_store.py
git commit -m "feat: add v4 sqlite event store"
```

## Task 3: Artifact Store

**Files:**
- Create: `src/codex_claude_orchestrator/v4/artifacts.py`
- Create: `tests/v4/test_artifacts.py`

- [ ] **Step 1: Write failing artifact store tests**

Create `tests/v4/test_artifacts.py`:

```python
from pathlib import Path

from codex_claude_orchestrator.v4.artifacts import ArtifactStore


def test_artifact_store_writes_json_and_text(tmp_path: Path):
    store = ArtifactStore(tmp_path / "artifacts")

    json_ref = store.write_json("turns/turn-1/output.json", {"status": "ok"})
    text_ref = store.write_text("turns/turn-1/transcript.txt", "hello")

    assert json_ref.path == "turns/turn-1/output.json"
    assert text_ref.path == "turns/turn-1/transcript.txt"
    assert (tmp_path / "artifacts" / json_ref.path).read_text(encoding="utf-8").strip() == '{"status": "ok"}'
    assert (tmp_path / "artifacts" / text_ref.path).read_text(encoding="utf-8") == "hello"


def test_artifact_store_blocks_path_traversal(tmp_path: Path):
    store = ArtifactStore(tmp_path / "artifacts")

    for bad_name in ("../secret.txt", "/tmp/secret.txt", "", "turns/../secret.txt"):
        try:
            store.write_text(bad_name, "bad")
        except ValueError as exc:
            assert "artifact path must be relative" in str(exc)
        else:
            raise AssertionError(f"expected ValueError for {bad_name}")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_artifacts.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.artifacts'`.

- [ ] **Step 3: Implement artifact store**

Create `src/codex_claude_orchestrator/v4/artifacts.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    path: str
    media_type: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "media_type": self.media_type}


class ArtifactStore:
    def __init__(self, root: Path):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def write_json(self, artifact_path: str, payload: Any) -> ArtifactRef:
        path = self._resolve(artifact_path)
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return ArtifactRef(path=artifact_path, media_type="application/json")

    def write_text(self, artifact_path: str, content: str, *, media_type: str = "text/plain") -> ArtifactRef:
        path = self._resolve(artifact_path)
        path.write_text(content, encoding="utf-8")
        return ArtifactRef(path=artifact_path, media_type=media_type)

    def read_text(self, artifact_path: str) -> str:
        return self._resolve(artifact_path).read_text(encoding="utf-8", errors="replace")

    def _resolve(self, artifact_path: str) -> Path:
        candidate = Path(artifact_path)
        if not artifact_path or candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("artifact path must be relative and stay inside artifact root")
        resolved = (self._root / candidate).resolve()
        root = self._root.resolve()
        if root != resolved and root not in resolved.parents:
            raise ValueError("artifact path must be relative and stay inside artifact root")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_artifacts.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/artifacts.py tests/v4/test_artifacts.py
git commit -m "feat: add v4 artifact store"
```

## Task 4: Runtime Adapter Models

**Files:**
- Create: `src/codex_claude_orchestrator/v4/runtime.py`
- Create: `tests/v4/test_runtime_models.py`

- [ ] **Step 1: Write failing runtime model tests**

Create `tests/v4/test_runtime_models.py`:

```python
from codex_claude_orchestrator.v4.runtime import RuntimeEvent, TurnEnvelope, WorkerSpec


def test_turn_envelope_builds_idempotency_key():
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement the patch",
        expected_marker="<<<CODEX_TURN_DONE crew=crew-1 worker=worker-1 phase=source round=1>>>",
    )

    assert turn.idempotency_key == "crew-1/worker-1/turn-1/source"


def test_runtime_event_to_dict():
    event = RuntimeEvent(
        type="output.chunk",
        turn_id="turn-1",
        worker_id="worker-1",
        payload={"text": "hello"},
    )

    assert event.to_dict()["payload"] == {"text": "hello"}


def test_worker_spec_requires_runtime_type():
    try:
        WorkerSpec(crew_id="crew-1", worker_id="worker-1", runtime_type="", contract_id="contract-1")
    except ValueError as exc:
        assert "runtime_type is required" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_runtime_models.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.runtime'`.

- [ ] **Step 3: Implement runtime models and protocol**

Create `src/codex_claude_orchestrator/v4/runtime.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from codex_claude_orchestrator.v4.events import normalize


@dataclass(frozen=True, slots=True)
class WorkerSpec:
    crew_id: str
    worker_id: str
    runtime_type: str
    contract_id: str
    workspace_path: str = ""
    terminal_pane: str = ""
    transcript_artifact: str = ""
    capabilities: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.runtime_type:
            raise ValueError("runtime_type is required")


@dataclass(frozen=True, slots=True)
class WorkerHandle:
    crew_id: str
    worker_id: str
    runtime_type: str
    status: str = "running"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TurnEnvelope:
    crew_id: str
    worker_id: str
    turn_id: str
    round_id: str
    phase: str
    message: str
    expected_marker: str
    deadline_at: str = ""
    attempt: int = 1

    @property
    def idempotency_key(self) -> str:
        return f"{self.crew_id}/{self.worker_id}/{self.turn_id}/{self.phase}"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: bool
    marker: str
    reason: str = ""
    artifact_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    type: str
    turn_id: str
    worker_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    artifact_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return normalize(self)


@dataclass(frozen=True, slots=True)
class CancellationResult:
    cancelled: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class StopResult:
    stopped: bool
    reason: str = ""


class RuntimeAdapter(Protocol):
    def spawn_worker(self, spec: WorkerSpec) -> WorkerHandle: ...
    def deliver_turn(self, turn: TurnEnvelope) -> DeliveryResult: ...
    def watch_turn(self, turn: TurnEnvelope) -> Iterable[RuntimeEvent]: ...
    def collect_artifacts(self, turn: TurnEnvelope) -> list[str]: ...
    def cancel_turn(self, turn: TurnEnvelope) -> CancellationResult: ...
    def stop_worker(self, worker_id: str) -> StopResult: ...
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_runtime_models.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/runtime.py tests/v4/test_runtime_models.py
git commit -m "feat: add v4 runtime adapter models"
```

## Task 5: Turn Service and Idempotency Guard

**Files:**
- Modify: `src/codex_claude_orchestrator/v4/event_store.py`
- Create: `src/codex_claude_orchestrator/v4/turns.py`
- Modify: `tests/v4/test_event_store.py`
- Create: `tests/v4/test_turns.py`

**Execution correction:** Delivery ownership must be durable, not only process-local. Add an atomic event-store claim helper such as `append_claim(...) -> tuple[AgentEvent, bool]`, where `bool` is true only when the event was newly inserted. `TurnService` must claim `turn.delivery_started` before calling the adapter. If another process already claimed the same attempt and no terminal delivered/failed event exists, return an in-progress result and do not send the turn again. Attempt-specific `turn.requested` and `turn.delivery_started` keys are required; `turn.delivered` remains the logical-turn success dedupe key. Failed same-attempt replay must return the stored failure, including stored marker.

- [ ] **Step 1: Write failing turn service tests**

Create `tests/v4/test_turns.py`:

```python
from pathlib import Path

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.runtime import DeliveryResult, TurnEnvelope
from codex_claude_orchestrator.v4.turns import TurnService


class FakeAdapter:
    def __init__(self):
        self.delivered = []

    def deliver_turn(self, turn):
        self.delivered.append(turn.turn_id)
        return DeliveryResult(delivered=True, marker=turn.expected_marker, reason="sent")


def make_turn():
    return TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )


def test_turn_service_records_request_and_delivery(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    adapter = FakeAdapter()
    service = TurnService(event_store=store, adapter=adapter)

    result = service.request_and_deliver(make_turn())

    assert result.delivered is True
    assert [event.type for event in store.list_stream("crew-1")] == ["turn.requested", "turn.delivery_started", "turn.delivered"]


def test_turn_service_does_not_deliver_same_turn_twice(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    adapter = FakeAdapter()
    service = TurnService(event_store=store, adapter=adapter)

    service.request_and_deliver(make_turn())
    service.request_and_deliver(make_turn())

    assert adapter.delivered == ["turn-1"]
    assert [event.type for event in store.list_stream("crew-1")].count("turn.delivered") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_turns.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.turns'`.

- [ ] **Step 3: Implement turn service**

Create `src/codex_claude_orchestrator/v4/turns.py`:

```python
from __future__ import annotations

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.runtime import DeliveryResult, RuntimeAdapter, TurnEnvelope


class TurnService:
    def __init__(self, *, event_store: SQLiteEventStore, adapter: RuntimeAdapter):
        self._events = event_store
        self._adapter = adapter

    def request_and_deliver(self, turn: TurnEnvelope) -> DeliveryResult:
        self._events.append(
            stream_id=turn.crew_id,
            type="turn.requested",
            crew_id=turn.crew_id,
            worker_id=turn.worker_id,
            turn_id=turn.turn_id,
            idempotency_key=f"{turn.idempotency_key}/requested",
            payload={
                "round_id": turn.round_id,
                "phase": turn.phase,
                "message": turn.message,
                "expected_marker": turn.expected_marker,
                "deadline_at": turn.deadline_at,
                "attempt": turn.attempt,
            },
        )
        delivered_event = self._events.get_by_idempotency_key(f"{turn.idempotency_key}/delivered")
        if delivered_event is not None:
            return DeliveryResult(
                delivered=True,
                marker=delivered_event.payload.get("marker", turn.expected_marker),
                reason="already delivered",
                artifact_refs=list(delivered_event.artifact_refs),
            )
        self._events.append(
            stream_id=turn.crew_id,
            type="turn.delivery_started",
            crew_id=turn.crew_id,
            worker_id=turn.worker_id,
            turn_id=turn.turn_id,
            idempotency_key=f"{turn.idempotency_key}/delivery-started",
        )
        result = self._adapter.deliver_turn(turn)
        if result.delivered:
            self._events.append(
                stream_id=turn.crew_id,
                type="turn.delivered",
                crew_id=turn.crew_id,
                worker_id=turn.worker_id,
                turn_id=turn.turn_id,
                idempotency_key=f"{turn.idempotency_key}/delivered",
                payload={"marker": result.marker, "reason": result.reason},
                artifact_refs=result.artifact_refs,
            )
        else:
            self._events.append(
                stream_id=turn.crew_id,
                type="turn.delivery_failed",
                crew_id=turn.crew_id,
                worker_id=turn.worker_id,
                turn_id=turn.turn_id,
                idempotency_key=f"{turn.idempotency_key}/delivery-failed/{turn.attempt}",
                payload={"reason": result.reason},
                artifact_refs=result.artifact_refs,
            )
        return result
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_turns.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/turns.py tests/v4/test_turns.py
git commit -m "feat: add v4 turn delivery service"
```

## Task 6: Completion Detector

**Files:**
- Create: `src/codex_claude_orchestrator/v4/completion.py`
- Create: `tests/v4/test_completion.py`

- [ ] **Step 1: Write failing completion detector tests**

Create `tests/v4/test_completion.py`:

```python
from codex_claude_orchestrator.v4.completion import CompletionDetector
from codex_claude_orchestrator.v4.runtime import RuntimeEvent, TurnEnvelope


def make_turn():
    return TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="<<<DONE turn-1>>>",
    )


def test_completion_detector_completes_on_expected_marker():
    detector = CompletionDetector()

    result = detector.evaluate(
        make_turn(),
        [RuntimeEvent(type="output.chunk", turn_id="turn-1", worker_id="worker-1", payload={"text": "ok\n<<<DONE turn-1>>>"})],
    )

    assert result.event_type == "turn.completed"
    assert result.reason == "expected marker detected"


def test_completion_detector_detects_contract_marker_mismatch():
    detector = CompletionDetector()

    result = detector.evaluate(
        make_turn(),
        [RuntimeEvent(type="output.chunk", turn_id="turn-1", worker_id="worker-1", payload={"text": "<<<CODEX_TURN_DONE crew=crew-1 contract=source_write>>>"})],
        contract_marker="<<<CODEX_TURN_DONE crew=crew-1 contract=source_write>>>",
    )

    assert result.event_type == "turn.inconclusive"
    assert "expected turn marker was missing" in result.reason


def test_completion_detector_times_out_without_marker():
    detector = CompletionDetector()

    result = detector.evaluate(make_turn(), [], timed_out=True)

    assert result.event_type == "turn.timeout"
    assert result.reason == "deadline reached before completion evidence"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_completion.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.completion'`.

- [ ] **Step 3: Implement completion detector**

Create `src/codex_claude_orchestrator/v4/completion.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

from codex_claude_orchestrator.v4.runtime import RuntimeEvent, TurnEnvelope


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    event_type: str
    reason: str
    evidence_refs: list[str] = field(default_factory=list)


class CompletionDetector:
    def evaluate(
        self,
        turn: TurnEnvelope,
        events: list[RuntimeEvent],
        *,
        contract_marker: str = "",
        timed_out: bool = False,
    ) -> CompletionDecision:
        text = "\n".join(str(event.payload.get("text", "")) for event in events if event.type == "output.chunk")
        evidence_refs = [ref for event in events for ref in event.artifact_refs]
        if turn.expected_marker and turn.expected_marker in text:
            return CompletionDecision("turn.completed", "expected marker detected", evidence_refs)
        if contract_marker and contract_marker in text:
            return CompletionDecision("turn.inconclusive", "contract marker found but expected turn marker was missing", evidence_refs)
        if any(event.type == "process.exited" for event in events):
            exit_event = next(event for event in events if event.type == "process.exited")
            return CompletionDecision("turn.failed", str(exit_event.payload.get("reason", "process exited before completion")), evidence_refs)
        if timed_out:
            return CompletionDecision("turn.timeout", "deadline reached before completion evidence", evidence_refs)
        return CompletionDecision("turn.inconclusive", "completion evidence not found", evidence_refs)
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_completion.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/completion.py tests/v4/test_completion.py
git commit -m "feat: add v4 completion detector"
```

## Task 7: Output Ingestor

**Files:**
- Create: `src/codex_claude_orchestrator/v4/ingest.py`
- Create: `tests/v4/test_ingest.py`

- [ ] **Step 1: Write failing ingestor tests**

Create `tests/v4/test_ingest.py`:

```python
from codex_claude_orchestrator.v4.ingest import OutputIngestor


def test_output_ingestor_slices_current_turn_after_prior_marker():
    ingestor = OutputIngestor()
    text = (
        "old review\n"
        "<<<CODEX_TURN_DONE crew=crew-1 worker=w phase=review round=1>>>\n"
        "current output\n"
        "<<<CODEX_TURN_DONE crew=crew-1 worker=w phase=review round=2>>>\n"
        "prompt tail"
    )

    current = ingestor.current_turn_text(
        text,
        expected_marker="<<<CODEX_TURN_DONE crew=crew-1 worker=w phase=review round=2>>>",
    )

    assert current == "current output\n"


def test_output_ingestor_builds_chunk_events():
    ingestor = OutputIngestor()

    events = ingestor.to_output_events(
        turn_id="turn-1",
        worker_id="worker-1",
        text="a\nb",
        artifact_ref="turns/turn-1/transcript.txt",
    )

    assert [event.payload["text"] for event in events] == ["a", "b"]
    assert events[0].artifact_refs == ["turns/turn-1/transcript.txt"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_ingest.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.ingest'`.

- [ ] **Step 3: Implement output ingestor**

Create `src/codex_claude_orchestrator/v4/ingest.py`:

```python
from __future__ import annotations

from codex_claude_orchestrator.v4.runtime import RuntimeEvent


TURN_DONE_PREFIX = "<<<CODEX_TURN_DONE"


class OutputIngestor:
    def current_turn_text(self, text: str, *, expected_marker: str) -> str:
        before_marker = text.split(expected_marker, 1)[0]
        prior_start = before_marker.rfind(TURN_DONE_PREFIX)
        if prior_start == -1:
            return before_marker
        prior_end = before_marker.find(">>>", prior_start)
        if prior_end == -1:
            return before_marker[prior_start + len(TURN_DONE_PREFIX):]
        return before_marker[prior_end + len(">>>"):]

    def to_output_events(
        self,
        *,
        turn_id: str,
        worker_id: str,
        text: str,
        artifact_ref: str = "",
    ) -> list[RuntimeEvent]:
        artifact_refs = [artifact_ref] if artifact_ref else []
        return [
            RuntimeEvent(
                type="output.chunk",
                turn_id=turn_id,
                worker_id=worker_id,
                payload={"text": line},
                artifact_refs=artifact_refs,
            )
            for line in text.splitlines()
        ]
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_ingest.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/ingest.py tests/v4/test_ingest.py
git commit -m "feat: add v4 output ingestor"
```

## Task 8: Claude Code Tmux Adapter

**Files:**
- Create: `src/codex_claude_orchestrator/v4/adapters/__init__.py`
- Create: `src/codex_claude_orchestrator/v4/adapters/tmux_claude.py`
- Create: `tests/v4/test_tmux_claude_adapter.py`

- [ ] **Step 1: Write failing adapter tests**

Create `tests/v4/test_tmux_claude_adapter.py`:

```python
from codex_claude_orchestrator.v4.adapters.tmux_claude import ClaudeCodeTmuxAdapter
from codex_claude_orchestrator.v4.runtime import TurnEnvelope, WorkerSpec


class FakeNativeSession:
    def __init__(self):
        self.sent = []
        self.observations = []

    def send(self, **kwargs):
        self.sent.append(kwargs)
        return {"marker": kwargs["turn_marker"], "message": kwargs["message"]}

    def observe(self, **kwargs):
        self.observations.append(kwargs)
        return {
            "snapshot": "hello\nmarker-1",
            "marker": "marker-1",
            "marker_seen": True,
            "transcript_artifact": "turns/turn-1/transcript.txt",
        }


def test_tmux_adapter_delivers_turn_to_native_session():
    native = FakeNativeSession()
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    result = adapter.deliver_turn(turn)

    assert result.delivered is True
    assert native.sent[0]["turn_marker"] == "marker-1"


def test_tmux_adapter_watch_turn_emits_output_and_marker_events():
    native = FakeNativeSession()
    adapter = ClaudeCodeTmuxAdapter(native_session=native)
    adapter.register_worker(WorkerSpec(crew_id="crew-1", worker_id="worker-1", runtime_type="tmux_claude", contract_id="contract-1", terminal_pane="pane-1"))
    turn = TurnEnvelope(
        crew_id="crew-1",
        worker_id="worker-1",
        turn_id="turn-1",
        round_id="round-1",
        phase="source",
        message="Implement",
        expected_marker="marker-1",
    )

    events = list(adapter.watch_turn(turn))

    assert [event.type for event in events] == ["output.chunk", "marker.detected"]
    assert events[-1].payload["marker"] == "marker-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_tmux_claude_adapter.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.adapters'`.

- [ ] **Step 3: Implement tmux adapter wrapper**

Create `src/codex_claude_orchestrator/v4/adapters/__init__.py`:

```python
"""V4 runtime adapters."""
```

Create `src/codex_claude_orchestrator/v4/adapters/tmux_claude.py`:

```python
from __future__ import annotations

from codex_claude_orchestrator.v4.runtime import (
    CancellationResult,
    DeliveryResult,
    RuntimeEvent,
    StopResult,
    TurnEnvelope,
    WorkerHandle,
    WorkerSpec,
)


class ClaudeCodeTmuxAdapter:
    def __init__(self, *, native_session):
        self._native_session = native_session
        self._workers: dict[str, WorkerSpec] = {}

    def register_worker(self, spec: WorkerSpec) -> WorkerHandle:
        self._workers[spec.worker_id] = spec
        return WorkerHandle(crew_id=spec.crew_id, worker_id=spec.worker_id, runtime_type=spec.runtime_type)

    def spawn_worker(self, spec: WorkerSpec) -> WorkerHandle:
        return self.register_worker(spec)

    def deliver_turn(self, turn: TurnEnvelope) -> DeliveryResult:
        worker = self._workers.get(turn.worker_id)
        terminal_pane = worker.terminal_pane if worker else turn.worker_id
        result = self._native_session.send(
            terminal_pane=terminal_pane,
            message=turn.message,
            turn_marker=turn.expected_marker,
        )
        return DeliveryResult(delivered=True, marker=result.get("marker", turn.expected_marker), reason="sent to tmux pane")

    def watch_turn(self, turn: TurnEnvelope):
        worker = self._workers.get(turn.worker_id)
        terminal_pane = worker.terminal_pane if worker else turn.worker_id
        observation = self._native_session.observe(
            terminal_pane=terminal_pane,
            lines=200,
            turn_marker=turn.expected_marker,
        )
        text = observation.get("snapshot", "")
        artifact_refs = [observation.get("transcript_artifact", "")] if observation.get("transcript_artifact") else []
        if text:
            yield RuntimeEvent(
                type="output.chunk",
                turn_id=turn.turn_id,
                worker_id=turn.worker_id,
                payload={"text": text},
                artifact_refs=artifact_refs,
            )
        if observation.get("marker_seen", False):
            yield RuntimeEvent(
                type="marker.detected",
                turn_id=turn.turn_id,
                worker_id=turn.worker_id,
                payload={"marker": observation.get("marker", turn.expected_marker), "source": "tmux"},
                artifact_refs=artifact_refs,
            )

    def collect_artifacts(self, turn: TurnEnvelope) -> list[str]:
        worker = self._workers.get(turn.worker_id)
        return [worker.transcript_artifact] if worker and worker.transcript_artifact else []

    def cancel_turn(self, turn: TurnEnvelope) -> CancellationResult:
        return CancellationResult(cancelled=False, reason="tmux Claude turn cancellation is not supported by this adapter")

    def stop_worker(self, worker_id: str) -> StopResult:
        return StopResult(stopped=False, reason="worker stop is delegated to existing worker pool")
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_tmux_claude_adapter.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/adapters/__init__.py src/codex_claude_orchestrator/v4/adapters/tmux_claude.py tests/v4/test_tmux_claude_adapter.py
git commit -m "feat: add v4 claude tmux adapter"
```

## Task 9: Projections

**Files:**
- Create: `src/codex_claude_orchestrator/v4/projections.py`
- Create: `tests/v4/test_projections.py`

- [ ] **Step 1: Write failing projection tests**

Create `tests/v4/test_projections.py`:

```python
from pathlib import Path

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.projections import CrewProjection


def test_projection_builds_turn_status_from_events(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(stream_id="crew-1", type="crew.started", crew_id="crew-1", payload={"goal": "Fix tests"})
    store.append(stream_id="crew-1", type="turn.requested", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")
    store.append(stream_id="crew-1", type="turn.delivered", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")
    store.append(stream_id="crew-1", type="turn.completed", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")

    projection = CrewProjection.from_events(store.list_stream("crew-1"))

    assert projection.crew_id == "crew-1"
    assert projection.goal == "Fix tests"
    assert projection.turns["turn-1"].status == "completed"


def test_projection_reports_waiting_turn():
    projection = CrewProjection.from_events([])

    assert projection.status == "empty"
    assert projection.turns == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_projections.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.projections'`.

- [ ] **Step 3: Implement projections**

Create `src/codex_claude_orchestrator/v4/projections.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

from codex_claude_orchestrator.v4.events import AgentEvent


@dataclass(slots=True)
class TurnProjection:
    turn_id: str
    worker_id: str
    status: str
    last_event_type: str


@dataclass(slots=True)
class CrewProjection:
    crew_id: str = ""
    goal: str = ""
    status: str = "empty"
    turns: dict[str, TurnProjection] = field(default_factory=dict)

    @classmethod
    def from_events(cls, events: list[AgentEvent]) -> "CrewProjection":
        projection = cls()
        for event in events:
            if event.crew_id:
                projection.crew_id = event.crew_id
            if event.type == "crew.started":
                projection.status = "running"
                projection.goal = str(event.payload.get("goal", ""))
                continue
            if event.type.startswith("turn.") and event.turn_id:
                projection.turns[event.turn_id] = TurnProjection(
                    turn_id=event.turn_id,
                    worker_id=event.worker_id,
                    status=event.type.split(".", 1)[1],
                    last_event_type=event.type,
                )
                projection.status = "running"
            if event.type == "crew.ready_for_accept":
                projection.status = "ready"
            if event.type == "human.required":
                projection.status = "needs_human"
            if event.type == "crew.accepted":
                projection.status = "accepted"
        return projection
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_projections.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/projections.py tests/v4/test_projections.py
git commit -m "feat: add v4 crew projections"
```

## Task 10: Reconciler

**Files:**
- Create: `src/codex_claude_orchestrator/v4/reconciler.py`
- Create: `tests/v4/test_reconciler.py`

- [ ] **Step 1: Write failing reconciler tests**

Create `tests/v4/test_reconciler.py`:

```python
from pathlib import Path

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.reconciler import Reconciler


def test_reconciler_marks_delivered_turn_without_completion_as_inconclusive(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(stream_id="crew-1", type="turn.requested", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")
    store.append(stream_id="crew-1", type="turn.delivered", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")

    event = Reconciler(event_store=store).reconcile_turn("crew-1", "turn-1")

    assert event.type == "turn.inconclusive"
    assert "delivered without completion" in event.payload["reason"]


def test_reconciler_does_not_duplicate_existing_completion(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(stream_id="crew-1", type="turn.delivered", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")
    store.append(stream_id="crew-1", type="turn.completed", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")

    event = Reconciler(event_store=store).reconcile_turn("crew-1", "turn-1")

    assert event is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_reconciler.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.reconciler'`.

- [ ] **Step 3: Implement reconciler**

Create `src/codex_claude_orchestrator/v4/reconciler.py`:

```python
from __future__ import annotations

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.events import AgentEvent


TERMINAL_TURN_EVENTS = {"turn.completed", "turn.failed", "turn.timeout", "turn.cancelled", "turn.inconclusive"}


class Reconciler:
    def __init__(self, *, event_store: SQLiteEventStore):
        self._events = event_store

    def reconcile_turn(self, crew_id: str, turn_id: str) -> AgentEvent | None:
        events = self._events.list_by_turn(turn_id)
        if any(event.type in TERMINAL_TURN_EVENTS for event in events):
            return None
        delivered = next((event for event in events if event.type == "turn.delivered"), None)
        if delivered is None:
            return None
        return self._events.append(
            stream_id=crew_id,
            type="turn.inconclusive",
            crew_id=crew_id,
            worker_id=delivered.worker_id,
            turn_id=turn_id,
            idempotency_key=f"{crew_id}/{turn_id}/reconcile/inconclusive",
            payload={"reason": "turn was delivered without completion evidence"},
            artifact_refs=delivered.artifact_refs,
        )
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_reconciler.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/reconciler.py tests/v4/test_reconciler.py
git commit -m "feat: add v4 recovery reconciler"
```

## Task 11: Verification Adapter

**Files:**
- Create: `src/codex_claude_orchestrator/v4/adapters/verification.py`
- Create: `tests/v4/test_verification_adapter.py`

- [ ] **Step 1: Write failing verification adapter tests**

Create `tests/v4/test_verification_adapter.py`:

```python
from pathlib import Path

from codex_claude_orchestrator.v4.adapters.verification import VerificationAdapter
from codex_claude_orchestrator.v4.artifacts import ArtifactStore


def test_verification_adapter_records_passed_command(tmp_path: Path):
    adapter = VerificationAdapter(artifact_store=ArtifactStore(tmp_path / "artifacts"))

    result = adapter.run(command=".venv/bin/python -c 'print(123)'", cwd=tmp_path, verification_id="verification-1")

    assert result["passed"] is True
    assert result["exit_code"] == 0
    assert result["stdout_artifact"] == "verification/verification-1/stdout.txt"
    assert "123" in (tmp_path / "artifacts" / result["stdout_artifact"]).read_text(encoding="utf-8")


def test_verification_adapter_records_failed_command(tmp_path: Path):
    adapter = VerificationAdapter(artifact_store=ArtifactStore(tmp_path / "artifacts"))

    result = adapter.run(command=".venv/bin/python -c 'import sys; print(\"bad\"); sys.exit(3)'", cwd=tmp_path, verification_id="verification-2")

    assert result["passed"] is False
    assert result["exit_code"] == 3
    assert "bad" in (tmp_path / "artifacts" / result["stdout_artifact"]).read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_verification_adapter.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.adapters.verification'`.

- [ ] **Step 3: Implement verification adapter**

Create `src/codex_claude_orchestrator/v4/adapters/verification.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path

from codex_claude_orchestrator.v4.artifacts import ArtifactStore


class VerificationAdapter:
    def __init__(self, *, artifact_store: ArtifactStore):
        self._artifacts = artifact_store

    def run(self, *, command: str, cwd: Path, verification_id: str) -> dict:
        result = subprocess.run(command, cwd=cwd, shell=True, text=True, capture_output=True, check=False)
        stdout_artifact = self._artifacts.write_text(
            f"verification/{verification_id}/stdout.txt",
            result.stdout,
        )
        stderr_artifact = self._artifacts.write_text(
            f"verification/{verification_id}/stderr.txt",
            result.stderr,
        )
        return {
            "verification_id": verification_id,
            "command": command,
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "summary": f"command {'passed' if result.returncode == 0 else 'failed'}: exit code {result.returncode}",
            "stdout_artifact": stdout_artifact.path,
            "stderr_artifact": stderr_artifact.path,
        }
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_verification_adapter.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/adapters/verification.py tests/v4/test_verification_adapter.py
git commit -m "feat: add v4 verification adapter"
```

## Task 12: V4 Gate Event Bridge

**Files:**
- Create: `src/codex_claude_orchestrator/v4/gates.py`
- Create: `tests/v4/test_workflow.py`

- [ ] **Step 1: Write failing gate bridge tests**

Create `tests/v4/test_workflow.py` with:

```python
from codex_claude_orchestrator.crew.gates import GateResult
from codex_claude_orchestrator.crew.review_verdict import ReviewVerdict
from codex_claude_orchestrator.v4.gates import GateEventBuilder


def test_gate_event_builder_builds_scope_event_payload():
    event = GateEventBuilder().scope_evaluated(
        crew_id="crew-1",
        round_id="round-1",
        worker_id="worker-1",
        result=GateResult(status="pass", reason="inside scope", evidence_refs=["changes.json"]),
    )

    assert event.type == "scope.evaluated"
    assert event.payload["status"] == "pass"
    assert event.artifact_refs == ["changes.json"]


def test_gate_event_builder_builds_review_event_payload():
    event = GateEventBuilder().review_verdict(
        crew_id="crew-1",
        round_id="round-1",
        worker_id="worker-review",
        verdict=ReviewVerdict(status="warn", summary="minor", findings=["risk"], evidence_refs=["review.json"]),
    )

    assert event.type == "review.verdict"
    assert event.payload["status"] == "warn"
    assert event.payload["findings"] == ["risk"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_workflow.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.gates'`.

- [ ] **Step 3: Implement gate event builder**

Create `src/codex_claude_orchestrator/v4/gates.py`:

```python
from __future__ import annotations

from codex_claude_orchestrator.crew.gates import GateResult
from codex_claude_orchestrator.crew.readiness import ReadinessReport
from codex_claude_orchestrator.crew.review_verdict import ReviewVerdict
from codex_claude_orchestrator.v4.events import AgentEvent


class GateEventBuilder:
    def scope_evaluated(self, *, crew_id: str, round_id: str, worker_id: str, result: GateResult) -> AgentEvent:
        return AgentEvent(
            event_id=f"event-{crew_id}-{round_id}-scope",
            stream_id=crew_id,
            sequence=1,
            type="scope.evaluated",
            crew_id=crew_id,
            worker_id=worker_id,
            payload={"round_id": round_id, **result.to_dict()},
            artifact_refs=list(result.evidence_refs),
        )

    def review_verdict(self, *, crew_id: str, round_id: str, worker_id: str, verdict: ReviewVerdict) -> AgentEvent:
        return AgentEvent(
            event_id=f"event-{crew_id}-{round_id}-review",
            stream_id=crew_id,
            sequence=1,
            type="review.verdict",
            crew_id=crew_id,
            worker_id=worker_id,
            payload={"round_id": round_id, **verdict.to_dict()},
            artifact_refs=list(verdict.evidence_refs),
        )

    def readiness_evaluated(self, *, crew_id: str, round_id: str, worker_id: str, report: ReadinessReport) -> AgentEvent:
        return AgentEvent(
            event_id=f"event-{crew_id}-{round_id}-readiness",
            stream_id=crew_id,
            sequence=1,
            type="readiness.evaluated",
            crew_id=crew_id,
            worker_id=worker_id,
            payload=report.to_dict(),
            artifact_refs=list(report.evidence_refs),
        )
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_workflow.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/gates.py tests/v4/test_workflow.py
git commit -m "feat: add v4 gate event bridge"
```

## Task 13: Workflow Engine Skeleton

**Correction note from Task 12 review:** The original Task 12 sample creates `AgentEvent`
objects with `stream_id=crew_id` and `sequence=1`. That shape is not safe to append to
`SQLiteEventStore`, because the store owns per-stream sequence assignment and enforces
`UNIQUE(stream_id, sequence)`. The Task 12 implementation should therefore support a
store-backed mode that appends gate events through `SQLiteEventStore.append(...)` with
stable idempotency keys, while any no-store builder output must be treated as detached
event/template data rather than a crew-stream event ready for insertion.

**Files:**
- Create: `src/codex_claude_orchestrator/v4/workflow.py`
- Modify: `tests/v4/test_workflow.py`

- [ ] **Step 1: Add failing workflow engine tests**

Append to `tests/v4/test_workflow.py`:

```python
from pathlib import Path

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.workflow import V4WorkflowEngine


def test_workflow_engine_starts_crew_once(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    engine = V4WorkflowEngine(event_store=store)

    first = engine.start_crew(crew_id="crew-1", goal="Fix tests")
    second = engine.start_crew(crew_id="crew-1", goal="Fix tests")

    assert first.event_id == second.event_id
    assert [event.type for event in store.list_stream("crew-1")] == ["crew.started"]


def test_workflow_engine_records_human_required(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    engine = V4WorkflowEngine(event_store=store)

    event = engine.require_human(crew_id="crew-1", reason="review verdict unknown", evidence_refs=["review.json"])

    assert event.type == "human.required"
    assert event.payload["reason"] == "review verdict unknown"
    assert event.artifact_refs == ["review.json"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_workflow.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.workflow'`.

- [ ] **Step 3: Implement workflow engine skeleton**

Create `src/codex_claude_orchestrator/v4/workflow.py`:

```python
from __future__ import annotations

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.events import AgentEvent


class V4WorkflowEngine:
    def __init__(self, *, event_store: SQLiteEventStore):
        self._events = event_store

    def start_crew(self, *, crew_id: str, goal: str) -> AgentEvent:
        return self._events.append(
            stream_id=crew_id,
            type="crew.started",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/crew.started",
            payload={"goal": goal},
        )

    def require_human(self, *, crew_id: str, reason: str, evidence_refs: list[str] | None = None) -> AgentEvent:
        return self._events.append(
            stream_id=crew_id,
            type="human.required",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/human.required/{reason}",
            payload={"reason": reason},
            artifact_refs=evidence_refs or [],
        )

    def mark_ready(self, *, crew_id: str, round_id: str, evidence_refs: list[str]) -> AgentEvent:
        return self._events.append(
            stream_id=crew_id,
            type="crew.ready_for_accept",
            crew_id=crew_id,
            idempotency_key=f"{crew_id}/{round_id}/ready",
            payload={"round_id": round_id},
            artifact_refs=evidence_refs,
        )
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_workflow.py -q
```

Expected: all workflow tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/workflow.py tests/v4/test_workflow.py
git commit -m "feat: add v4 workflow engine skeleton"
```

## Task 14: V4 Supervisor Facade

**Files:**
- Create: `src/codex_claude_orchestrator/v4/supervisor.py`
- Create: `tests/v4/test_supervisor.py`

- [ ] **Step 1: Write failing V4 supervisor tests**

Create `tests/v4/test_supervisor.py`:

```python
from pathlib import Path

from codex_claude_orchestrator.v4.artifacts import ArtifactStore
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.runtime import DeliveryResult, RuntimeEvent, TurnEnvelope
from codex_claude_orchestrator.v4.supervisor import V4Supervisor


class FakeAdapter:
    def __init__(self, events):
        self.events = events
        self.delivered = []

    def deliver_turn(self, turn: TurnEnvelope):
        self.delivered.append(turn.turn_id)
        return DeliveryResult(delivered=True, marker=turn.expected_marker, reason="sent")

    def watch_turn(self, turn: TurnEnvelope):
        return iter(self.events)


def test_v4_supervisor_runs_until_turn_completed(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter([
            RuntimeEvent(type="output.chunk", turn_id="turn-1", worker_id="worker-1", payload={"text": "done marker-1"}),
        ]),
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "turn_completed"
    assert [event.type for event in store.list_stream("crew-1")] == [
        "crew.started",
        "turn.requested",
        "turn.delivery_started",
        "turn.delivered",
        "output.chunk",
        "turn.completed",
    ]


def test_v4_supervisor_returns_waiting_for_inconclusive_turn(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    supervisor = V4Supervisor(
        event_store=store,
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        adapter=FakeAdapter([]),
    )

    result = supervisor.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "waiting"
    assert result["reason"] == "completion evidence not found"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_supervisor.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.v4.supervisor'`.

- [ ] **Step 3: Implement V4 supervisor facade**

Create `src/codex_claude_orchestrator/v4/supervisor.py`:

```python
from __future__ import annotations

from codex_claude_orchestrator.v4.artifacts import ArtifactStore
from codex_claude_orchestrator.v4.completion import CompletionDetector
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.runtime import RuntimeAdapter, TurnEnvelope
from codex_claude_orchestrator.v4.turns import TurnService
from codex_claude_orchestrator.v4.workflow import V4WorkflowEngine


class V4Supervisor:
    def __init__(self, *, event_store: SQLiteEventStore, artifact_store: ArtifactStore, adapter: RuntimeAdapter):
        self._events = event_store
        self._artifacts = artifact_store
        self._adapter = adapter
        self._turns = TurnService(event_store=event_store, adapter=adapter)
        self._workflow = V4WorkflowEngine(event_store=event_store)
        self._completion = CompletionDetector()

    def run_source_turn(
        self,
        *,
        crew_id: str,
        goal: str,
        worker_id: str,
        round_id: str,
        message: str,
        expected_marker: str,
    ) -> dict:
        self._workflow.start_crew(crew_id=crew_id, goal=goal)
        turn = TurnEnvelope(
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=f"{round_id}-{worker_id}-source",
            round_id=round_id,
            phase="source",
            message=message,
            expected_marker=expected_marker,
        )
        self._turns.request_and_deliver(turn)
        runtime_events = list(self._adapter.watch_turn(turn))
        for runtime_event in runtime_events:
            self._events.append(
                stream_id=crew_id,
                type=runtime_event.type,
                crew_id=crew_id,
                worker_id=runtime_event.worker_id,
                turn_id=runtime_event.turn_id,
                idempotency_key=f"{crew_id}/{runtime_event.turn_id}/{runtime_event.type}/{len(self._events.list_by_turn(runtime_event.turn_id))}",
                payload=runtime_event.payload,
                artifact_refs=runtime_event.artifact_refs,
            )
        decision = self._completion.evaluate(turn, runtime_events)
        self._events.append(
            stream_id=crew_id,
            type=decision.event_type,
            crew_id=crew_id,
            worker_id=worker_id,
            turn_id=turn.turn_id,
            idempotency_key=f"{crew_id}/{turn.turn_id}/{decision.event_type}",
            payload={"reason": decision.reason},
            artifact_refs=decision.evidence_refs,
        )
        if decision.event_type == "turn.completed":
            return {"crew_id": crew_id, "status": "turn_completed", "turn_id": turn.turn_id}
        return {"crew_id": crew_id, "status": "waiting", "turn_id": turn.turn_id, "reason": decision.reason}
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_supervisor.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/supervisor.py tests/v4/test_supervisor.py
git commit -m "feat: add v4 supervisor facade"
```

## Task 15: CLI Event Inspection

**Files:**
- Modify: `src/codex_claude_orchestrator/cli.py`
- Modify: `tests/cli/test_cli.py`

- [ ] **Step 1: Add failing CLI tests**

Append to `tests/cli/test_cli.py`:

```python
def test_cli_crew_events_lists_v4_events(tmp_path, capsys):
    from codex_claude_orchestrator.cli import main
    from codex_claude_orchestrator.v4.event_store import SQLiteEventStore

    store_path = tmp_path / ".orchestrator" / "v4" / "events.sqlite3"
    store = SQLiteEventStore(store_path)
    store.append(stream_id="crew-1", type="crew.started", crew_id="crew-1", payload={"goal": "Fix tests"})

    result = main(["crew", "events", "--repo", str(tmp_path), "--crew", "crew-1"])

    assert result == 0
    assert "crew.started" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/cli/test_cli.py::test_cli_crew_events_lists_v4_events -q
```

Expected: FAIL because `crew events` command does not exist.

- [ ] **Step 3: Add CLI command**

In `src/codex_claude_orchestrator/cli.py`, import:

```python
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
```

In `build_parser()`, under `crew_subparsers`, add:

```python
    crew_events = crew_subparsers.add_parser("events", help="List V4 crew events")
    crew_events.add_argument("--repo", required=True)
    crew_events.add_argument("--crew", required=True)
```

In the crew command handler, before other commands return:

```python
    if args.crew_command == "events":
        event_store = SQLiteEventStore(repo_root / ".orchestrator" / "v4" / "events.sqlite3")
        print(json.dumps([event.to_dict() for event in event_store.list_stream(args.crew)], ensure_ascii=False))
        return 0
```

- [ ] **Step 4: Run CLI test**

Run:

```bash
.venv/bin/python -m pytest tests/cli/test_cli.py::test_cli_crew_events_lists_v4_events -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/cli.py tests/cli/test_cli.py
git commit -m "feat: add v4 crew event inspection cli"
```

## Task 16: UI Event Projection

**Files:**
- Modify: `src/codex_claude_orchestrator/ui/server.py`
- Modify: `tests/ui/test_server.py`

- [ ] **Step 1: Add failing UI state test**

Append to `tests/ui/test_server.py`:

```python
def test_ui_state_includes_v4_event_summary(tmp_path):
    from codex_claude_orchestrator.ui.server import build_ui_state
    from codex_claude_orchestrator.v4.event_store import SQLiteEventStore

    store = SQLiteEventStore(tmp_path / ".orchestrator" / "v4" / "events.sqlite3")
    store.append(stream_id="crew-1", type="crew.started", crew_id="crew-1", payload={"goal": "Fix tests"})
    store.append(stream_id="crew-1", type="turn.completed", crew_id="crew-1", worker_id="worker-1", turn_id="turn-1")

    state = build_ui_state(tmp_path)

    assert state["v4"]["event_count"] == 2
    assert state["v4"]["crews"][0]["crew_id"] == "crew-1"
    assert state["v4"]["crews"][0]["status"] == "running"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/ui/test_server.py::test_ui_state_includes_v4_event_summary -q
```

Expected: FAIL because UI state has no `v4` key.

- [ ] **Step 3: Add V4 UI state projection**

In `src/codex_claude_orchestrator/ui/server.py`, import:

```python
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.projections import CrewProjection
```

Add helper:

```python
def build_v4_ui_state(repo_root: Path) -> dict:
    store_path = repo_root / ".orchestrator" / "v4" / "events.sqlite3"
    if not store_path.exists():
        return {"event_count": 0, "crews": []}
    store = SQLiteEventStore(store_path)
    crew_ids = sorted({event.crew_id for event in store.list_all() if event.crew_id})
    crews = []
    for crew_id in crew_ids:
        projection = CrewProjection.from_events(store.list_stream(crew_id))
        crews.append({"crew_id": crew_id, "status": projection.status, "turn_count": len(projection.turns)})
    return {"event_count": sum(len(store.list_stream(crew_id)) for crew_id in crew_ids), "crews": crews}
```

Add `list_all()` to `SQLiteEventStore` if not already present:

```python
    def list_all(self) -> list[AgentEvent]:
        with self._connect() as db:
            rows = db.execute("select * from events order by stream_id asc, sequence asc").fetchall()
        return [self._row_to_event(row) for row in rows]
```

In `build_ui_state(repo_root)`, include:

```python
        "v4": build_v4_ui_state(repo_root),
```

- [ ] **Step 4: Run UI test**

Run:

```bash
.venv/bin/python -m pytest tests/ui/test_server.py::test_ui_state_includes_v4_event_summary -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/ui/server.py src/codex_claude_orchestrator/v4/event_store.py tests/ui/test_server.py
git commit -m "feat: expose v4 events in ui state"
```

## Task 17: End-to-End V4 Recovery Scenario

**Files:**
- Modify: `tests/v4/test_supervisor.py`
- Modify: `src/codex_claude_orchestrator/v4/supervisor.py`

- [ ] **Step 1: Add failing recovery test**

Append to `tests/v4/test_supervisor.py`:

```python
def test_v4_supervisor_resume_does_not_redeliver_completed_turn(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    adapter = FakeAdapter([
        RuntimeEvent(type="output.chunk", turn_id="round-1-worker-1-source", worker_id="worker-1", payload={"text": "done marker-1"}),
    ])
    first = V4Supervisor(event_store=store, artifact_store=ArtifactStore(tmp_path / "artifacts"), adapter=adapter)

    first.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    resumed = V4Supervisor(event_store=store, artifact_store=ArtifactStore(tmp_path / "artifacts"), adapter=adapter)
    result = resumed.run_source_turn(
        crew_id="crew-1",
        goal="Fix tests",
        worker_id="worker-1",
        round_id="round-1",
        message="Implement",
        expected_marker="marker-1",
    )

    assert result["status"] == "turn_completed"
    assert adapter.delivered == ["round-1-worker-1-source"]
    assert [event.type for event in store.list_stream("crew-1")].count("turn.completed") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_supervisor.py::test_v4_supervisor_resume_does_not_redeliver_completed_turn -q
```

Expected: FAIL because `V4Supervisor` appends duplicate runtime/completion events.

- [ ] **Step 3: Add resume guard to supervisor**

In `V4Supervisor.run_source_turn()`, before `request_and_deliver`, inspect existing events:

```python
        existing_events = self._events.list_by_turn(turn.turn_id)
        if any(event.type == "turn.completed" for event in existing_events):
            return {"crew_id": crew_id, "status": "turn_completed", "turn_id": turn.turn_id, "resumed": True}
```

When appending runtime events, use deterministic idempotency keys based on runtime event index:

```python
        for index, runtime_event in enumerate(runtime_events):
            self._events.append(
                stream_id=crew_id,
                type=runtime_event.type,
                crew_id=crew_id,
                worker_id=runtime_event.worker_id,
                turn_id=runtime_event.turn_id,
                idempotency_key=f"{crew_id}/{runtime_event.turn_id}/runtime/{index}/{runtime_event.type}",
                payload=runtime_event.payload,
                artifact_refs=runtime_event.artifact_refs,
            )
```

- [ ] **Step 4: Run supervisor tests**

Run:

```bash
.venv/bin/python -m pytest tests/v4/test_supervisor.py -q
```

Expected: all V4 supervisor tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/codex_claude_orchestrator/v4/supervisor.py tests/v4/test_supervisor.py
git commit -m "feat: make v4 source turns resumable"
```

## Task 18: Final V4 Regression and Traceability

**Files:**
- Verify all files touched in Tasks 1-17.

- [ ] **Step 1: Run V4 test suite**

Run:

```bash
.venv/bin/python -m pytest tests/v4 -q
```

Expected: all V4 tests pass.

- [ ] **Step 2: Run integration-adjacent tests**

Run:

```bash
.venv/bin/python -m pytest tests/crew tests/runtime tests/cli/test_cli.py tests/ui/test_server.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass. If unrelated worktree refactor tests fail, record exact failing test names and rerun V4 plus integration-adjacent tests.

- [ ] **Step 4: Inspect changed file paths**

Run:

```bash
git diff --name-only HEAD~17..HEAD
```

Expected: changed paths are limited to:

```text
src/codex_claude_orchestrator/v4/
tests/v4/
src/codex_claude_orchestrator/cli.py
src/codex_claude_orchestrator/ui/server.py
tests/cli/test_cli.py
tests/ui/test_server.py
```

- [ ] **Step 5: Manual traceability check**

Verify:

```text
Event schema and SQLite event store -> Tasks 1-2
Artifact store and stable artifact refs -> Task 3
Runtime adapter interface -> Task 4
Turn idempotency guard -> Task 5
Completion detector -> Task 6
Output ingestor and current-turn slicing -> Task 7
Claude Code/tmux adapter -> Task 8
Projections and reconciler -> Tasks 9-10
Verification adapter -> Task 11
Gate event bridge -> Task 12
Workflow engine skeleton -> Task 13
V4 supervisor facade -> Tasks 14 and 17
CLI/UI event inspection -> Tasks 15-16
```

- [ ] **Step 6: Commit final verification note if docs were updated**

If no files changed during final verification, do not create an empty commit. If a traceability note is added to the plan, run:

```bash
git add docs/superpowers/plans/2026-05-01-codex-managed-claude-crew-v4.md
git commit -m "docs: record v4 implementation verification"
```
