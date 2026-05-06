from pathlib import Path

from codex_claude_orchestrator.state.blackboard import BlackboardStore
from codex_claude_orchestrator.crew.controller import CrewController
import pytest

from codex_claude_orchestrator.crew.models import (
    AuthorityLevel,
    BlackboardEntryType,
    CrewRecord,
    CrewStatus,
    DecisionAction,
    DecisionActionType,
    WorkerContract,
    WorkerRecord,
    WorkerRole,
    WorkerStatus,
    WorkspacePolicy,
)
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.core.models import WorkspaceMode
from codex_claude_orchestrator.crew.task_graph import TaskGraphPlanner


class FakeWorkerPool:
    def __init__(self):
        self.started = []
        self.sent = []
        self.observed = []
        self.attached = []
        self.stopped_workers = []
        self.stopped_crews = []
        self.pruned = []

    def start_worker(self, *, repo_root, crew, task, allow_dirty_base=False):
        self.started.append((repo_root, crew.crew_id, task.task_id, task.role_required, allow_dirty_base))
        return type("Worker", (), {"worker_id": f"worker-{task.role_required.value}"})()

    def ensure_worker(self, *, repo_root, crew, contract, task=None, allow_dirty_base=False):
        self.started.append((repo_root, crew.crew_id, task.task_id, task.role_required, allow_dirty_base))
        return type(
            "Worker",
            (),
            {
                "worker_id": f"worker-{contract.label}",
                "contract_id": contract.contract_id,
                "label": contract.label,
                "capabilities": contract.required_capabilities,
                "authority_level": contract.authority_level,
                "to_dict": lambda self: {
                    "worker_id": self.worker_id,
                    "contract_id": self.contract_id,
                    "label": self.label,
                    "capabilities": self.capabilities,
                    "authority_level": self.authority_level.value,
                },
            },
        )()

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

    def stop_worker(self, **kwargs):
        self.stopped_workers.append(kwargs)
        return {"terminal_session": "crew-1-worker-explorer", "stopped": True}

    def stop_crew(self, **kwargs):
        self.stopped_crews.append(kwargs)
        return {
            "crew_id": kwargs["crew_id"],
            "stopped_workers": [{"worker_id": "worker-explorer", "stopped": True}],
        }

    def prune_orphans(self, **kwargs):
        self.pruned.append(kwargs)
        return {"active_sessions": ["crew-1-worker-explorer"], "pruned_sessions": ["crew-worker-old"]}


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
    stopped = controller.stop_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id="worker-explorer", workspace_cleanup="remove")
    pruned = controller.prune_orphans(repo_root=repo_root)

    assert crew.status == CrewStatus.RUNNING
    assert pool.started[1][4] is False
    assert sent["marker_seen"] is True
    assert observed["snapshot"] == "Claude is reading files"
    assert attached["attach_command"] == "tmux attach -t crew-1-worker-explorer"
    assert tail["lines"] == ["worker transcript line"]
    assert status["running"] is True
    assert stopped["stopped"] is True
    assert pool.stopped_workers[0]["workspace_cleanup"] == "remove"
    assert pruned["pruned_sessions"] == ["crew-worker-old"]


def test_crew_controller_fake_flow_start_send_verify_challenge_accept(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    pool = FakeWorkerPool()

    class FakeVerificationRunner:
        def run(self, **kwargs):
            return {
                "verification_id": "verification-1",
                "command": kwargs["command"],
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
    assert pool.stopped_crews[0]["crew_id"] == "crew-1"


def test_controller_stop_cancels_crew_and_stops_workers(tmp_path: Path):
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
    )
    crew = controller.start(repo_root=repo_root, goal="Build V3 MVP", worker_roles=[WorkerRole.EXPLORER])

    stopped = controller.stop(repo_root=repo_root, crew_id=crew.crew_id)
    details = recorder.read_crew(crew.crew_id)

    assert stopped["status"] == "cancelled"
    assert details["crew"]["status"] == "cancelled"
    assert recorder.active_worker_ids(crew.crew_id) == []
    assert pool.stopped_crews[0]["crew_id"] == "crew-1"


def test_controller_rolls_back_started_workers_when_start_fails(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")

    class FailingWorkerPool(FakeWorkerPool):
        def start_worker(self, *, repo_root, crew, task, allow_dirty_base=False):
            if task.role_required is WorkerRole.IMPLEMENTER:
                raise RuntimeError("worktree failed")
            return super().start_worker(repo_root=repo_root, crew=crew, task=task, allow_dirty_base=allow_dirty_base)

    pool = FailingWorkerPool()
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}"),
        worker_pool=pool,
        crew_id_factory=lambda: "crew-1",
    )

    with pytest.raises(RuntimeError, match="worktree failed"):
        controller.start(
            repo_root=repo_root,
            goal="Build V3 MVP",
            worker_roles=[WorkerRole.EXPLORER, WorkerRole.IMPLEMENTER],
        )

    details = recorder.read_crew("crew-1")
    assert details["crew"]["status"] == "failed"
    assert recorder.active_worker_ids("crew-1") == []
    assert pool.stopped_crews[0]["crew_id"] == "crew-1"


def test_controller_verifies_implementer_worktree_by_default(tmp_path: Path):
    repo_root = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    repo_root.mkdir()
    worktree.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo=repo_root)
    recorder.start_crew(crew)
    recorder.append_worker(
        crew.crew_id,
        WorkerRecord(
            worker_id="worker-implementer",
            crew_id=crew.crew_id,
            role=WorkerRole.IMPLEMENTER,
            agent_profile="claude",
            native_session_id="native-implementer",
            terminal_session="crew-worker-implementer",
            terminal_pane="crew-worker-implementer:claude.0",
            transcript_artifact="workers/worker-implementer/transcript.txt",
            turn_marker="<<<CODEX_TURN_DONE status=ready_for_codex>>>",
            workspace_mode=WorkspaceMode.WORKTREE,
            workspace_path=worktree,
            status=WorkerStatus.RUNNING,
        ),
    )

    class CapturingVerificationRunner:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return {"passed": True, "cwd": str(kwargs["cwd"]), "target_worker_id": kwargs["target_worker_id"]}

    verification_runner = CapturingVerificationRunner()
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(),
        worker_pool=FakeWorkerPool(),
        verification_runner=verification_runner,
    )

    result = controller.verify(crew_id=crew.crew_id, command="pytest -q")

    assert result["passed"] is True
    assert verification_runner.calls[0]["cwd"] == worktree
    assert verification_runner.calls[0]["target_worker_id"] == "worker-implementer"


def test_controller_starts_dynamic_crew_and_ensures_contract_worker_with_snapshot(tmp_path: Path):
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
        entry_id_factory=lambda: "entry-dynamic",
    )
    contract = WorkerContract(
        contract_id="contract-source",
        label="targeted-code-editor",
        mission="Fix failing tests.",
        required_capabilities=["inspect_code", "edit_source"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
        spawn_reason="goal requires source edits",
    )

    crew = controller.start_dynamic(repo_root=repo_root, goal="Fix failing tests")
    worker = controller.ensure_worker(repo_root=repo_root, crew_id=crew.crew_id, contract=contract)
    snapshot = controller.write_team_snapshot(crew_id=crew.crew_id, last_decision={"action_type": "spawn_worker"})
    details = recorder.read_crew(crew.crew_id)

    assert worker["worker_id"] == "worker-targeted-code-editor"
    assert details["tasks"][0]["contract_id"] == "contract-source"
    assert snapshot["contracts_created"][0]["contract_id"] == "contract-source"
    assert snapshot["workers_spawned"] == []  # FakeWorkerPool doesn't record workers to recorder
    assert details["team_snapshot"]["resume_hint"] == "Read team_snapshot.json and blackboard before supervising."


def test_controller_appends_known_pitfall_to_crew_state(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Fix repeated failures")

    pitfall = controller.append_known_pitfall(
        crew_id=crew.crew_id,
        failure_class="verification_repeat",
        summary="pytest failed three times",
        guardrail="Escalate after three similar failures.",
        evidence_refs=["verification.json"],
    )
    details = recorder.read_crew(crew.crew_id)

    assert pitfall["failure_class"] == "verification_repeat"
    assert details["known_pitfalls"][0]["summary"] == "pytest failed three times"


def test_controller_records_gate_artifacts_and_blackboard_entries(tmp_path: Path):
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
        entry_id_factory=lambda: "entry-gate",
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Harden dynamic crew")

    artifact = controller.write_json_artifact(
        crew_id=crew.crew_id,
        artifact_name="gates/round-1/write_scope.json",
        payload={"status": "pass"},
    )
    entry = controller.record_blackboard_entry(
        crew_id=crew.crew_id,
        entry_type=BlackboardEntryType.DECISION,
        content="Readiness evaluated",
        evidence_refs=[artifact],
    )
    details = recorder.read_crew(crew.crew_id)

    assert artifact == "gates/round-1/write_scope.json"
    assert "gates/round-1/write_scope.json" in details["artifacts"]
    assert entry["type"] == "decision"
    assert details["blackboard"][-1]["content"] == "Readiness evaluated"
    assert details["blackboard"][-1]["evidence_refs"] == ["gates/round-1/write_scope.json"]


def test_controller_rejects_unsafe_artifact_names(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Harden dynamic crew")

    for artifact_name in ("../crew.json", "/tmp/x.json", ""):
        with pytest.raises(ValueError, match="unsafe artifact name"):
            controller.write_json_artifact(
                crew_id=crew.crew_id,
                artifact_name=artifact_name,
                payload={"status": "pass"},
            )


def test_controller_records_decision_action_to_decisions_jsonl(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Fix failing tests")
    action = DecisionAction(
        action_id="decision-1",
        crew_id=crew.crew_id,
        action_type=DecisionActionType.ACCEPT_READY,
        reason="verification passed",
    )

    recorded = controller.record_decision(crew_id=crew.crew_id, action=action)
    details = recorder.read_crew(crew.crew_id)

    assert recorded["action_type"] == "accept_ready"
    assert details["decisions"] == [recorded]


def test_controller_record_decision_dict_returns_normalized_payload(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Fix failing tests")

    recorded = controller.record_decision(
        crew_id=crew.crew_id,
        action={
            "action_id": "decision-1",
            "crew_id": crew.crew_id,
            "action_type": DecisionActionType.ACCEPT_READY,
            "reason": "verification passed",
        },
    )
    details = recorder.read_crew(crew.crew_id)

    assert recorded["crew_id"] == "crew-1"
    assert details["decisions"][0]["crew_id"] == "crew-1"
    assert recorded["action_type"] == "accept_ready"
    assert details["decisions"] == [recorded]


def test_controller_record_decision_rejects_mismatched_crew_id(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Fix failing tests")

    with pytest.raises(ValueError, match="decision crew_id mismatch"):
        controller.record_decision(
            crew_id=crew.crew_id,
            action={
                "action_id": "decision-1",
                "crew_id": "crew-2",
                "action_type": "accept_ready",
                "reason": "verification passed",
            },
        )


def test_controller_resume_context_collects_snapshot_and_replay_inputs(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
        entry_id_factory=lambda: "entry-resume",
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Resume dynamic crew")
    controller.record_decision(
        crew_id=crew.crew_id,
        action=DecisionAction(
            action_id="decision-1",
            crew_id=crew.crew_id,
            action_type=DecisionActionType.ACCEPT_READY,
            reason="verification passed",
        ),
    )
    controller.append_known_pitfall(
        crew_id=crew.crew_id,
        failure_class="verification_repeat",
        summary="pytest failed three times",
        guardrail="Escalate after three similar failures.",
    )

    context = controller.resume_context(crew_id=crew.crew_id)

    assert context["crew"]["crew_id"] == "crew-1"
    assert context["team_snapshot"]["resume_hint"] == "Read team_snapshot.json and blackboard before supervising."
    assert context["blackboard"][0]["entry_id"] == "entry-resume"
    assert context["decisions"][0]["action_type"] == "accept_ready"
    assert context["known_pitfalls"][0]["failure_class"] == "verification_repeat"
    assert context["resume_hint"] == "Replay decisions, protocol requests, and blackboard before sending the next worker turn."


# ---------------------------------------------------------------------------
# Dual-write tests: EventStore integration
# ---------------------------------------------------------------------------


class FakeEventStore:
    """In-memory event store for testing dual-write behavior."""

    def __init__(self):
        self.events: list[dict] = []

    def append(self, *, stream_id, type: str, crew_id="", worker_id="", **kwargs):
        event = {
            "stream_id": stream_id,
            "type": type,
            "crew_id": crew_id,
            "worker_id": worker_id,
            "idempotency_key": kwargs.get("idempotency_key", ""),
            "payload": kwargs.get("payload"),
        }
        self.events.append(event)
        return event

    def append_claim(self, **kwargs):
        return self.append(**kwargs), True

    def list_stream(self, stream_id, after_sequence=0):
        return [e for e in self.events if e["stream_id"] == stream_id]

    def list_by_turn(self, turn_id):
        return []

    def list_all(self):
        return list(self.events)

    def get_by_idempotency_key(self, key):
        for e in self.events:
            if e["idempotency_key"] == key:
                return e
        return None

    def health(self):
        return {"backend": "fake", "ok": True}


def test_controller_start_emits_crew_started_event(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    event_store = FakeEventStore()
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder, event_store=event_store),
        task_graph=TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}"),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
        entry_id_factory=lambda: "entry-1",
        event_store=event_store,
    )

    crew = controller.start(
        repo_root=repo_root,
        goal="Build V3 MVP",
        worker_roles=[WorkerRole.EXPLORER],
    )

    crew_events = [e for e in event_store.events if e["type"] == "crew.started"]
    assert len(crew_events) == 1
    assert crew_events[0]["crew_id"] == "crew-1"
    assert crew_events[0]["payload"]["goal"] == "Build V3 MVP"


def test_controller_start_dynamic_emits_crew_started_event(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    event_store = FakeEventStore()
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder, event_store=event_store),
        task_graph=TaskGraphPlanner(),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
        entry_id_factory=lambda: "entry-1",
        event_store=event_store,
    )

    crew = controller.start_dynamic(repo_root=repo_root, goal="Fix failing tests")

    crew_events = [e for e in event_store.events if e["type"] == "crew.started"]
    assert len(crew_events) == 1
    assert crew_events[0]["crew_id"] == "crew-1"
    assert crew_events[0]["payload"]["goal"] == "Fix failing tests"


def test_controller_stop_emits_crew_stopped_event(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    event_store = FakeEventStore()
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder, event_store=event_store),
        task_graph=TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}"),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
        entry_id_factory=lambda: "entry-1",
        event_store=event_store,
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Test stop")

    controller.stop(repo_root=repo_root, crew_id=crew.crew_id)

    stopped_events = [e for e in event_store.events if e["type"] == "crew.stopped"]
    assert len(stopped_events) == 1
    assert stopped_events[0]["crew_id"] == "crew-1"
    assert stopped_events[0]["payload"]["reason"] == "crew stopped by Codex"


def test_controller_accept_emits_crew_finalized_event(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    event_store = FakeEventStore()
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder, event_store=event_store),
        task_graph=TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}"),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
        entry_id_factory=lambda: "entry-1",
        event_store=event_store,
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Test accept")

    controller.accept(crew_id=crew.crew_id, summary="All tests pass")

    finalized_events = [e for e in event_store.events if e["type"] == "crew.finalized"]
    assert len(finalized_events) == 1
    assert finalized_events[0]["crew_id"] == "crew-1"
    assert finalized_events[0]["payload"]["status"] == "accepted"
    assert finalized_events[0]["payload"]["final_summary"] == "All tests pass"


def test_controller_record_decision_emits_decision_recorded_event(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    event_store = FakeEventStore()
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder, event_store=event_store),
        task_graph=TaskGraphPlanner(),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
        entry_id_factory=lambda: "entry-1",
        event_store=event_store,
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Test decisions")
    action = DecisionAction(
        action_id="decision-1",
        crew_id=crew.crew_id,
        action_type=DecisionActionType.ACCEPT_READY,
        reason="verification passed",
    )

    controller.record_decision(crew_id=crew.crew_id, action=action)

    decision_events = [e for e in event_store.events if e["type"] == "decision.recorded"]
    assert len(decision_events) == 1
    assert decision_events[0]["crew_id"] == "crew-1"
    assert decision_events[0]["idempotency_key"] == "crew-1/decision/decision-1"
    assert decision_events[0]["payload"]["action_type"] == "accept_ready"
    assert decision_events[0]["payload"]["reason"] == "verification passed"


def test_controller_challenge_emits_blackboard_entry_event(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    event_store = FakeEventStore()
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder, event_store=event_store),
        task_graph=TaskGraphPlanner(),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
        entry_id_factory=lambda: "entry-1",
        event_store=event_store,
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Test challenge")

    controller.challenge(crew_id=crew.crew_id, summary="Need more evidence")

    bb_events = [e for e in event_store.events if e["type"] == "blackboard.entry"]
    # One from start_dynamic initial decision, one from challenge
    assert len(bb_events) == 2
    risk_events = [e for e in bb_events if e["payload"].get("entry_type") == "risk"]
    assert len(risk_events) == 1
    assert risk_events[0]["crew_id"] == "crew-1"
    assert risk_events[0]["payload"]["content"] == "Need more evidence"


def test_controller_works_without_event_store(tmp_path: Path):
    """Verify no crash when event_store=None (backward compatibility)."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}"),
        worker_pool=FakeWorkerPool(),
        crew_id_factory=lambda: "crew-1",
        entry_id_factory=lambda: "entry-1",
    )

    crew = controller.start_dynamic(repo_root=repo_root, goal="No event store")
    controller.record_decision(
        crew_id=crew.crew_id,
        action=DecisionAction(
            action_id="decision-1",
            crew_id=crew.crew_id,
            action_type=DecisionActionType.ACCEPT_READY,
            reason="ok",
        ),
    )
    controller.stop(repo_root=repo_root, crew_id=crew.crew_id)

    # No assertion needed — just verify no exceptions were raised
    assert crew.crew_id == "crew-1"
