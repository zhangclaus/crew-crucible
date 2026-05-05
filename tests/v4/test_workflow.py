from pathlib import Path
from typing import get_type_hints

from codex_claude_orchestrator.crew.gates import GateResult
from codex_claude_orchestrator.crew.readiness import ReadinessReport
from codex_claude_orchestrator.crew.review_verdict import ReviewVerdict
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.event_store_protocol import EventStore
from codex_claude_orchestrator.v4.events import AgentEvent
from codex_claude_orchestrator.v4.gates import GateEventBuilder
from codex_claude_orchestrator.v4.workflow import V4WorkflowEngine


def test_gate_event_builder_depends_on_event_store_protocol():
    annotation = get_type_hints(GateEventBuilder.__init__)["event_store"]

    assert annotation == EventStore | None


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


def test_store_backed_gate_event_builder_appends_after_existing_crew_event(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.db")
    started = store.append(stream_id="crew-1", type="crew.started")
    builder = GateEventBuilder(event_store=store)

    scope = builder.scope_evaluated(
        crew_id="crew-1",
        round_id="round-1",
        worker_id="worker-1",
        result=GateResult(status="pass", reason="inside scope", evidence_refs=["changes.json"]),
    )
    review = builder.review_verdict(
        crew_id="crew-1",
        round_id="round-1",
        worker_id="worker-review",
        verdict=ReviewVerdict(
            status="warn",
            summary="minor",
            findings=["risk"],
            evidence_refs=["review.json"],
        ),
    )

    assert [event.sequence for event in store.list_stream("crew-1")] == [1, 2, 3]
    assert [event.event_id for event in store.list_stream("crew-1")] == [
        started.event_id,
        scope.event_id,
        review.event_id,
    ]


def test_store_backed_gate_event_builder_dedupes_identical_payloads_and_appends_changed_payloads(
    tmp_path: Path,
):
    store = SQLiteEventStore(tmp_path / "events.db")
    builder = GateEventBuilder(event_store=store)

    first = builder.scope_evaluated(
        crew_id="crew-1",
        round_id="round-1",
        worker_id="worker-1",
        result=GateResult(status="pass", reason="inside scope", evidence_refs=["changes.json"]),
    )
    duplicate = builder.scope_evaluated(
        crew_id="crew-1",
        round_id="round-1",
        worker_id="worker-1",
        result=GateResult(status="pass", reason="inside scope", evidence_refs=["changes.json"]),
    )
    changed = builder.scope_evaluated(
        crew_id="crew-1",
        round_id="round-1",
        worker_id="worker-1",
        result=GateResult(
            status="challenge",
            reason="outside scope",
            evidence_refs=["changes-v2.json"],
        ),
    )

    assert duplicate.event_id == first.event_id
    assert changed.event_id != first.event_id
    assert [event.sequence for event in store.list_stream("crew-1")] == [1, 2]


def test_detached_gate_event_builder_uses_detached_stream_id():
    event = GateEventBuilder().scope_evaluated(
        crew_id="crew-1",
        round_id="round-1",
        worker_id="worker-1",
        result=GateResult(status="pass", reason="inside scope", evidence_refs=["changes.json"]),
    )

    assert event.stream_id.startswith("detached/")
    assert event.stream_id != "crew-1"


def test_readiness_event_payload_uses_method_round_and_worker_ids():
    report = ReadinessReport(
        round_id="stale-round",
        worker_id="stale-worker",
        contract_id="contract-1",
        status="ready",
        scope_status="pass",
        review_status="ok",
        verification_status="pass",
        evidence_refs=["readiness.json"],
    )

    event = GateEventBuilder().readiness_evaluated(
        crew_id="crew-1",
        round_id="round-1",
        worker_id="worker-1",
        report=report,
    )

    assert event.payload["round_id"] == "round-1"
    assert event.payload["worker_id"] == "worker-1"


def test_workflow_engine_starts_crew_once(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    engine = V4WorkflowEngine(event_store=store)

    first = engine.start_crew(crew_id="crew-1", goal="Fix tests")
    second = engine.start_crew(crew_id="crew-1", goal="Fix tests")

    assert first.event_id == second.event_id
    assert [event.type for event in store.list_stream("crew-1")] == ["crew.started"]


def test_workflow_engine_rejects_changed_crew_goal(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    engine = V4WorkflowEngine(event_store=store)

    engine.start_crew(crew_id="crew-1", goal="Fix tests")

    try:
        engine.start_crew(crew_id="crew-1", goal="Ship feature")
    except ValueError as exc:
        assert str(exc) == "crew already started with different goal"
    else:
        raise AssertionError("expected changed crew goal to be rejected")

    events = store.list_stream("crew-1")
    assert [event.type for event in events] == ["crew.started"]
    assert events[0].payload["goal"] == "Fix tests"


def test_workflow_engine_revalidates_crew_goal_after_append_dedupe():
    class RacingEventStore:
        def get_by_idempotency_key(self, idempotency_key: str) -> None:
            return None

        def append(self, **kwargs: object) -> AgentEvent:
            return AgentEvent(
                event_id="evt-existing",
                stream_id="crew-1",
                sequence=1,
                type="crew.started",
                crew_id="crew-1",
                idempotency_key="crew-1/crew.started",
                payload={"goal": "Fix tests"},
            )

    engine = V4WorkflowEngine(event_store=RacingEventStore())

    try:
        engine.start_crew(crew_id="crew-1", goal="Ship feature")
    except ValueError as exc:
        assert str(exc) == "crew already started with different goal"
    else:
        raise AssertionError("expected append-deduped crew goal conflict to be rejected")


def test_workflow_engine_records_human_required(tmp_path: Path):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    engine = V4WorkflowEngine(event_store=store)

    event = engine.require_human(
        crew_id="crew-1",
        reason="review verdict unknown",
        evidence_refs=["review.json"],
    )

    assert event.type == "human.required"
    assert event.payload["reason"] == "review verdict unknown"
    assert event.artifact_refs == ["review.json"]


def test_workflow_engine_human_required_dedupes_same_evidence_and_appends_changed_evidence(
    tmp_path: Path,
):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    engine = V4WorkflowEngine(event_store=store)

    first = engine.require_human(
        crew_id="crew-1",
        reason="review verdict unknown",
        evidence_refs=["review.json"],
    )
    duplicate = engine.require_human(
        crew_id="crew-1",
        reason="review verdict unknown",
        evidence_refs=["review.json"],
    )
    changed = engine.require_human(
        crew_id="crew-1",
        reason="review verdict unknown",
        evidence_refs=["review-v2.json"],
    )

    assert duplicate.event_id == first.event_id
    assert changed.event_id != first.event_id
    assert [event.sequence for event in store.list_stream("crew-1")] == [1, 2]


def test_workflow_engine_mark_ready_dedupes_same_evidence_and_appends_changed_evidence(
    tmp_path: Path,
):
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    engine = V4WorkflowEngine(event_store=store)

    first = engine.mark_ready(
        crew_id="crew-1",
        round_id="round-1",
        evidence_refs=["readiness.json"],
    )
    duplicate = engine.mark_ready(
        crew_id="crew-1",
        round_id="round-1",
        evidence_refs=["readiness.json"],
    )
    changed = engine.mark_ready(
        crew_id="crew-1",
        round_id="round-1",
        evidence_refs=["readiness-v2.json"],
    )

    assert duplicate.event_id == first.event_id
    assert changed.event_id != first.event_id
    assert [event.sequence for event in store.list_stream("crew-1")] == [1, 2]
