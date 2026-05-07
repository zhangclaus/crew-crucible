from pathlib import Path

from codex_claude_orchestrator.state.blackboard import BlackboardStore
from codex_claude_orchestrator.crew.models import (
    AuthorityLevel,
    CrewRecord,
    CrewStatus,
    CrewTaskRecord,
    WorkerContract,
    WorkerRecord,
    WorkerRole,
    WorkerStatus,
    WorkspacePolicy,
)
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.core.models import WorkspaceAllocation, WorkspaceMode
from codex_claude_orchestrator.workers.pool import WorkerPool


class FakeWorktreeManager:
    def __init__(self):
        self.prepared = []
        self.cleaned = []

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

    def cleanup(self, *, repo_root, allocation, remove=False):
        self.cleaned.append({"repo_root": repo_root, "allocation": allocation, "remove": remove})
        return {"removed": remove, "path": str(allocation.path)}


class FakeNativeSession:
    def __init__(self):
        self.starts = []
        self.sends = []
        self.observes = []
        self.stops = []
        self.prunes = []

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

    def stop(self, **kwargs):
        self.stops.append(kwargs)
        return {"terminal_session": kwargs["terminal_session"], "stopped": True}

    def prune_orphans(self, **kwargs):
        self.prunes.append(kwargs)
        return {"active_sessions": sorted(kwargs["active_sessions"]), "pruned_sessions": ["crew-worker-old"]}

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


def test_worker_pool_rejects_reuse_when_write_scope_is_incompatible(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Edit src", repo=repo_root)
    recorder.start_crew(crew)
    recorder.append_worker(
        crew.crew_id,
        WorkerRecord(
            worker_id="worker-source-docs",
            crew_id=crew.crew_id,
            role=WorkerRole.IMPLEMENTER,
            agent_profile="claude",
            native_session_id="native-docs",
            terminal_session="session-docs",
            terminal_pane="session-docs:claude.0",
            transcript_artifact="workers/worker-source-docs/transcript.txt",
            turn_marker="marker",
            workspace_mode=WorkspaceMode.WORKTREE,
            workspace_path=str(repo_root),
            capabilities=["edit_source"],
            authority_level=AuthorityLevel.SOURCE_WRITE,
            write_scope=["docs/"],
            status=WorkerStatus.RUNNING,
        ),
    )
    recorder.update_crew(crew.crew_id, {"active_worker_ids": ["worker-source-docs"]})
    pool = WorkerPool(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        worktree_manager=FakeWorktreeManager(),
        native_session=FakeNativeSession(),
    )

    worker = pool.find_compatible_worker(
        crew.crew_id,
        WorkerContract(
            contract_id="contract-src",
            label="source",
            mission="Edit src",
            required_capabilities=["edit_source"],
            authority_level=AuthorityLevel.SOURCE_WRITE,
            workspace_policy=WorkspacePolicy.WORKTREE,
            write_scope=["src/"],
        ),
    )

    assert worker is None


def test_worker_pool_can_stop_workers_and_prune_orphan_tmux_sessions(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo=repo_root)
    recorder.start_crew(crew)
    fake_native = FakeNativeSession()
    pool = WorkerPool(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        worktree_manager=FakeWorktreeManager(),
        native_session=fake_native,
        worker_id_factory=lambda role: f"worker-{role.value}",
    )
    for role in (WorkerRole.EXPLORER, WorkerRole.REVIEWER):
        pool.start_worker(
            repo_root=repo_root,
            crew=crew,
            task=CrewTaskRecord(
                task_id=f"task-{role.value}",
                crew_id=crew.crew_id,
                title=role.value,
                instructions="Work.",
                role_required=role,
            ),
        )
    recorder.update_crew(
        crew.crew_id,
        {
            "status": CrewStatus.RUNNING.value,
            "active_worker_ids": ["worker-explorer", "worker-reviewer"],
        },
    )
    stale_crew = CrewRecord(crew_id="crew-stale", root_goal="old", repo=repo_root)
    recorder.start_crew(stale_crew)
    recorder.append_worker(
        stale_crew.crew_id,
        WorkerRecord(
            worker_id="worker-old",
            crew_id=stale_crew.crew_id,
            role=WorkerRole.EXPLORER,
            agent_profile="claude",
            native_session_id="native-old",
            terminal_session="crew-worker-old",
            terminal_pane="crew-worker-old:claude.0",
            transcript_artifact="workers/worker-old/transcript.txt",
            turn_marker="<<<CODEX_TURN_DONE status=ready_for_codex>>>",
            workspace_mode=WorkspaceMode.READONLY,
            workspace_path=repo_root,
            status=WorkerStatus.RUNNING,
        ),
    )

    stopped_worker = pool.stop_worker(repo_root=repo_root, crew_id=crew.crew_id, worker_id="worker-explorer")
    after_worker_stop = recorder.read_crew(crew.crew_id)
    active_after_single_stop = recorder.active_worker_ids(crew.crew_id)
    stopped_crew = pool.stop_crew(repo_root=repo_root, crew_id=crew.crew_id)
    after_crew_stop = recorder.read_crew(crew.crew_id)
    pruned = pool.prune_orphans(repo_root=repo_root)

    assert stopped_worker["stopped"] is True
    assert active_after_single_stop == ["worker-reviewer"]
    assert next(worker for worker in after_worker_stop["workers"] if worker["worker_id"] == "worker-explorer")["status"] == "stopped"
    assert [item["terminal_session"] for item in stopped_crew["stopped_workers"]] == [
        "crew-1-worker-explorer",
        "crew-1-worker-reviewer",
    ]
    assert recorder.active_worker_ids(crew.crew_id) == []
    assert {worker["status"] for worker in after_crew_stop["workers"]} == {"stopped"}
    assert pruned["active_sessions"] == []
    assert pruned["pruned_sessions"] == ["crew-worker-old"]
    assert fake_native.stops[0]["terminal_session"] == "crew-1-worker-explorer"


def test_worker_pool_stop_worker_can_remove_clean_worktree_when_requested(tmp_path: Path):
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
    )
    worker = pool.start_worker(
        repo_root=repo_root,
        crew=crew,
        task=CrewTaskRecord(
            task_id="task-implementer",
            crew_id=crew.crew_id,
            title="Implement",
            instructions="Work.",
            role_required=WorkerRole.IMPLEMENTER,
        ),
    )
    recorder.update_crew(crew.crew_id, {"status": CrewStatus.RUNNING.value, "active_worker_ids": [worker.worker_id]})

    stopped = pool.stop_worker(
        repo_root=repo_root,
        crew_id=crew.crew_id,
        worker_id=worker.worker_id,
        workspace_cleanup="remove",
    )

    assert stopped["workspace_cleanup"]["removed"] is True
    assert fake_worktree.cleaned[0]["remove"] is True
    assert fake_native.stops[0]["terminal_session"] == "crew-1-worker-implementer"


def test_worker_pool_observe_records_turn_event_and_routes_codex_messages(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Build dynamic V3", repo=repo_root)
    recorder.start_crew(crew)

    class MessagingNativeSession(FakeNativeSession):
        def observe(self, **kwargs):
            return {
                "snapshot": (
                    "done\n"
                    "<<<CODEX_MESSAGE\n"
                    "to: codex\n"
                    "type: question\n"
                    "body: Need approval before editing src/api.py.\n"
                    ">>>\n"
                    f"{kwargs['turn_marker']}"
                ),
                "marker_seen": True,
                "marker": kwargs["turn_marker"],
            }

    pool = WorkerPool(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        worktree_manager=FakeWorktreeManager(),
        native_session=MessagingNativeSession(),
        worker_id_factory=lambda role: "worker-source",
        entry_id_factory=lambda: "entry-worker",
        event_id_factory=lambda: "event-turn",
        message_id_factory=lambda: "msg-turn",
        thread_id_factory=lambda: "thread-turn",
    )
    contract = WorkerContract(
        contract_id="contract-source",
        label="targeted-code-editor",
        mission="Edit source.",
        required_capabilities=["inspect_code", "edit_source"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
    )
    worker = pool.ensure_worker(repo_root=repo_root, crew=crew, contract=contract)

    observation = pool.observe_worker(
        repo_root=repo_root,
        crew_id=crew.crew_id,
        worker_id=worker.worker_id,
        turn_marker="<<<CODEX_TURN_DONE turn=1>>>",
    )
    details = recorder.read_crew(crew.crew_id)

    assert observation["marker_seen"] is True
    assert observation["message_blocks"][0]["body"] == "Need approval before editing src/api.py."
    assert details["messages"][0]["from"] == "worker-source"
    assert details["messages"][0]["to"] == "codex"
    assert details["events"][0]["type"] == "worker_turn_observed"
    assert details["events"][0]["worker_id"] == "worker-source"


def test_worker_pool_observe_creates_and_resolves_protocol_requests_from_codex_messages(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Build dynamic V3", repo=repo_root)
    recorder.start_crew(crew)

    class ProtocolNativeSession(FakeNativeSession):
        def __init__(self):
            super().__init__()
            self.snapshots = [
                (
                    "<<<CODEX_MESSAGE\n"
                    "to: codex\n"
                    "type: plan_request\n"
                    "request_id: req-plan\n"
                    "requires_response: true\n"
                    "body: I need approval to edit src/api.py.\n"
                    ">>>\n"
                    "<<<CODEX_TURN_DONE turn=1>>>"
                ),
                (
                    "<<<CODEX_MESSAGE\n"
                    "to: worker-source\n"
                    "type: plan_response\n"
                    "request_id: req-plan\n"
                    "response_status: approved\n"
                    "body: Approved after scope review.\n"
                    ">>>\n"
                    "<<<CODEX_TURN_DONE turn=2>>>"
                ),
            ]

        def observe(self, **kwargs):
            return {
                "snapshot": self.snapshots.pop(0),
                "marker_seen": True,
                "marker": kwargs["turn_marker"],
            }

    pool = WorkerPool(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        worktree_manager=FakeWorktreeManager(),
        native_session=ProtocolNativeSession(),
        worker_id_factory=lambda role: "worker-source",
        entry_id_factory=lambda: "entry-worker",
        event_id_factory=lambda: "event-turn",
        message_id_factory=lambda: "msg-turn",
        thread_id_factory=lambda: "thread-turn",
    )
    contract = WorkerContract(
        contract_id="contract-source",
        label="targeted-code-editor",
        mission="Edit source.",
        required_capabilities=["inspect_code", "edit_source"],
        authority_level=AuthorityLevel.SOURCE_WRITE,
        workspace_policy=WorkspacePolicy.WORKTREE,
    )
    worker = pool.ensure_worker(repo_root=repo_root, crew=crew, contract=contract)

    pool.observe_worker(
        repo_root=repo_root,
        crew_id=crew.crew_id,
        worker_id=worker.worker_id,
        turn_marker="<<<CODEX_TURN_DONE turn=1>>>",
    )
    pool.observe_worker(
        repo_root=repo_root,
        crew_id=crew.crew_id,
        worker_id=worker.worker_id,
        turn_marker="<<<CODEX_TURN_DONE turn=2>>>",
    )
    details = recorder.read_crew(crew.crew_id)

    assert [request["status"] for request in details["protocol_requests"]] == ["pending", "approved"]
    assert details["protocol_requests"][0]["request_id"] == "req-plan"
    assert details["protocol_requests"][0]["subject"] == "I need approval to edit src/api.py."


def _make_pool_with_running_worker(tmp_path: Path, *, status: str = "running", worker_id: str = "worker-explorer"):
    """Helper: create a pool with a single worker in the given status."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Goal", repo=repo_root)
    recorder.start_crew(crew)
    recorder.append_worker(
        crew.crew_id,
        WorkerRecord(
            worker_id=worker_id,
            crew_id=crew.crew_id,
            role=WorkerRole.EXPLORER,
            agent_profile="claude",
            native_session_id="native-1",
            terminal_session="session-1",
            terminal_pane="session-1:claude.0",
            transcript_artifact="workers/{}/transcript.txt".format(worker_id),
            turn_marker="marker",
            workspace_mode=WorkspaceMode.READONLY,
            workspace_path=str(repo_root),
            status=WorkerStatus.RUNNING if status == "running" else WorkerStatus(status),
        ),
    )
    if status == "running":
        recorder.update_worker(crew.crew_id, worker_id, {"status": status})
    recorder.update_crew(crew.crew_id, {"active_worker_ids": [worker_id]})
    pool = WorkerPool(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        worktree_manager=FakeWorktreeManager(),
        native_session=FakeNativeSession(),
        event_id_factory=lambda: "event-claim",
    )
    return pool, crew, recorder


def test_claim_worker_transitions_running_to_busy(tmp_path: Path):
    pool, crew, recorder = _make_pool_with_running_worker(tmp_path, status="running")

    pool.claim_worker(crew.crew_id, "worker-explorer")

    worker = next(w for w in recorder.read_crew(crew.crew_id)["workers"] if w["worker_id"] == "worker-explorer")
    assert worker["status"] == "busy"
    event = recorder.read_crew(crew.crew_id)["events"][-1]
    assert event["type"] == "worker_claimed"
    assert event["worker_id"] == "worker-explorer"


def test_release_worker_transitions_busy_to_idle(tmp_path: Path):
    pool, crew, recorder = _make_pool_with_running_worker(tmp_path, status="busy")

    pool.release_worker(crew.crew_id, "worker-explorer")

    worker = next(w for w in recorder.read_crew(crew.crew_id)["workers"] if w["worker_id"] == "worker-explorer")
    assert worker["status"] == "idle"
    event = recorder.read_crew(crew.crew_id)["events"][-1]
    assert event["type"] == "worker_released"


def test_claim_worker_rejects_busy_worker(tmp_path: Path):
    pool, crew, recorder = _make_pool_with_running_worker(tmp_path, status="busy")

    import pytest
    with pytest.raises(ValueError, match="Cannot claim worker"):
        pool.claim_worker(crew.crew_id, "worker-explorer")


def test_release_worker_is_idempotent_when_not_busy(tmp_path: Path):
    pool, crew, recorder = _make_pool_with_running_worker(tmp_path, status="running")
    events_before = len(recorder.read_crew(crew.crew_id)["events"])

    pool.release_worker(crew.crew_id, "worker-explorer")

    worker = next(w for w in recorder.read_crew(crew.crew_id)["workers"] if w["worker_id"] == "worker-explorer")
    assert worker["status"] == "running"
    assert len(recorder.read_crew(crew.crew_id)["events"]) == events_before


def test_find_compatible_worker_excludes_busy(tmp_path: Path):
    pool, crew, recorder = _make_pool_with_running_worker(tmp_path, status="busy")
    contract = WorkerContract(
        contract_id="c1",
        label="explorer",
        mission="Explore.",
        required_capabilities=[],
        authority_level=AuthorityLevel.READONLY,
        workspace_policy=WorkspacePolicy.READONLY,
    )

    result = pool.find_compatible_worker(crew.crew_id, contract)

    assert result is None


def test_find_compatible_worker_finds_idle_worker(tmp_path: Path):
    pool, crew, recorder = _make_pool_with_running_worker(tmp_path, status="idle")
    contract = WorkerContract(
        contract_id="c1",
        label="explorer",
        mission="Explore.",
        required_capabilities=[],
        authority_level=AuthorityLevel.READONLY,
        workspace_policy=WorkspacePolicy.READONLY,
    )

    result = pool.find_compatible_worker(crew.crew_id, contract)

    assert result is not None
    assert result["worker_id"] == "worker-explorer"


def test_recover_stale_busy_workers_resets_to_idle(tmp_path: Path):
    pool, crew, recorder = _make_pool_with_running_worker(tmp_path, status="busy")

    # Simulate stale busy by directly patching the worker record in the JSONL file.
    # (update_worker always sets updated_at to utc_now(), so we write directly.)
    from datetime import UTC, datetime, timedelta
    stale_time = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    path = tmp_path / "repo" / ".orchestrator" / "crews" / crew.crew_id / "workers.jsonl"
    import json
    workers = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for w in workers:
        if w["worker_id"] == "worker-explorer":
            w["updated_at"] = stale_time
    path.write_text("".join(json.dumps(w) + "\n" for w in workers))

    recovered = recorder.recover_stale_busy_workers(crew.crew_id, max_busy_seconds=300)

    assert recovered == ["worker-explorer"]
    worker = next(w for w in recorder.read_crew(crew.crew_id)["workers"] if w["worker_id"] == "worker-explorer")
    assert worker["status"] == "idle"
    event = recorder.read_crew(crew.crew_id)["events"][-1]
    assert event["type"] == "worker_recovered"


def test_recover_stale_busy_workers_ignores_fresh_busy(tmp_path: Path):
    pool, crew, recorder = _make_pool_with_running_worker(tmp_path, status="busy")

    # Worker is fresh — no recovery.
    recovered = recorder.recover_stale_busy_workers(crew.crew_id, max_busy_seconds=300)

    assert recovered == []
    worker = next(w for w in recorder.read_crew(crew.crew_id)["workers"] if w["worker_id"] == "worker-explorer")
    assert worker["status"] == "busy"


def test_prune_orphans_recovers_stale_busy_workers(tmp_path: Path):
    pool, crew, recorder = _make_pool_with_running_worker(tmp_path, status="busy")
    recorder.update_crew(crew.crew_id, {"status": "running"})

    from datetime import UTC, datetime, timedelta
    import json
    stale_time = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    path = tmp_path / "repo" / ".orchestrator" / "crews" / crew.crew_id / "workers.jsonl"
    workers = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for w in workers:
        if w["worker_id"] == "worker-explorer":
            w["updated_at"] = stale_time
    path.write_text("".join(json.dumps(w) + "\n" for w in workers))

    result = pool.prune_orphans(repo_root=tmp_path)

    assert "recovered_workers" in result
    assert "worker-explorer" in result["recovered_workers"]
    worker = next(w for w in recorder.read_crew(crew.crew_id)["workers"] if w["worker_id"] == "worker-explorer")
    assert worker["status"] == "idle"


def test_stop_crew_with_workspace_cleanup(tmp_path: Path):
    """stop_crew(workspace_cleanup='remove') cleans up each worker's worktree."""
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
    )
    # Start two implementer workers so we get worktree allocations
    for i, role in enumerate((WorkerRole.IMPLEMENTER, WorkerRole.IMPLEMENTER)):
        pool.start_worker(
            repo_root=repo_root,
            crew=crew,
            task=CrewTaskRecord(
                task_id=f"task-{i}",
                crew_id=crew.crew_id,
                title=f"Implement {i}",
                instructions="Work.",
                role_required=role,
            ),
        )
    recorder.update_crew(
        crew.crew_id,
        {"status": CrewStatus.RUNNING.value, "active_worker_ids": ["worker-implementer", "worker-implementer"]},
    )

    result = pool.stop_crew(repo_root=repo_root, crew_id=crew.crew_id, workspace_cleanup="remove")

    assert result["crew_id"] == "crew-1"
    assert len(result["stopped_workers"]) == 2
    for entry in result["stopped_workers"]:
        assert entry["workspace_cleanup"]["removed"] is True
    assert len(fake_worktree.cleaned) == 2
    assert all(c["remove"] is True for c in fake_worktree.cleaned)


def test_stop_crew_default_keep_does_not_clean(tmp_path: Path):
    """stop_crew() default workspace_cleanup='keep' does not clean worktrees."""
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
    )
    pool.start_worker(
        repo_root=repo_root,
        crew=crew,
        task=CrewTaskRecord(
            task_id="task-explorer",
            crew_id=crew.crew_id,
            title="Explore",
            instructions="Read.",
            role_required=WorkerRole.EXPLORER,
        ),
    )

    result = pool.stop_crew(repo_root=repo_root, crew_id=crew.crew_id)

    assert result["stopped_workers"][0]["workspace_cleanup"] == {"removed": False, "reason": "keep policy"}
    assert len(fake_worktree.cleaned) == 0
