from __future__ import annotations

from pathlib import Path

from codex_claude_orchestrator.crew.models import WorkerRole
from codex_claude_orchestrator.v4.crew_runner import V4CrewRunner
from codex_claude_orchestrator.v4.event_store import SQLiteEventStore


def test_v4_crew_runner_supervise_completes_turn_verifies_and_marks_ready(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    controller = FakeController([{"passed": True, "summary": "command passed"}])
    supervisor = FakeV4Supervisor(
        [
            {"status": "turn_completed", "turn_id": "round-1-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-1-worker-review-review"},
        ],
        event_store=store,
        review_summaries=[
            "<<<CODEX_REVIEW\nverdict: OK\nsummary: patch matches spec and quality bar\nfindings:\n>>>"
        ],
    )

    result = V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=store,
    ).supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
        max_rounds=1,
    )

    assert result["status"] == "ready_for_codex_accept"
    assert result["runtime"] == "v4"
    assert controller.changes_called == [{"crew_id": "crew-1", "worker_id": "worker-source"}]
    assert controller.verify_called == [
        {"crew_id": "crew-1", "command": "pytest -q", "worker_id": "worker-source"}
    ]
    assert supervisor.registered[0].worker_id == "worker-source"
    assert supervisor.turns[0]["worker_id"] == "worker-source"
    assert supervisor.turns[0]["expected_marker"].startswith("<<<CODEX_TURN_DONE crew=crew-1")
    assert [event.type for event in store.list_stream("crew-1")] == [
        "worker.patch.recorded",
        "worker.result.recorded",
        "worker.outbox.detected",
        "review.completed",
        "verification.passed",
        "crew.ready_for_accept",
    ]
    assert (
        tmp_path
        / ".orchestrator/crews/crew-1/artifacts/v4/workers/worker-source/results/round-1-worker-source-source.json"
    ).exists()


def test_v4_crew_runner_prefers_high_quality_compatible_source_worker(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    store.append(
        stream_id="crew-1",
        type="worker.quality_updated",
        crew_id="crew-1",
        worker_id="worker-low",
        payload={"worker_id": "worker-low", "score_delta": -3},
    )
    store.append(
        stream_id="crew-1",
        type="worker.quality_updated",
        crew_id="crew-1",
        worker_id="worker-high",
        payload={"worker_id": "worker-high", "score_delta": 4},
    )
    controller = FakeController(
        [{"passed": True, "summary": "command passed"}],
        workers=[
            _source_worker(worker_id="worker-low"),
            _source_worker(worker_id="worker-high"),
            _review_worker(),
        ],
    )
    supervisor = FakeV4Supervisor(
        [
            {"status": "turn_completed", "turn_id": "round-1-worker-high-source"},
            {"status": "turn_completed", "turn_id": "round-1-worker-review-review"},
        ],
        event_store=store,
        review_summaries=[
            "<<<CODEX_REVIEW\nverdict: OK\nsummary: patch matches spec and quality bar\nfindings:\n>>>"
        ],
    )

    result = V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=store,
    ).supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
    )

    assert result["status"] == "ready_for_codex_accept"
    assert supervisor.turns[0]["worker_id"] == "worker-high"


def test_v4_crew_runner_does_not_reuse_incompatible_source_worker(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    controller = FakeController(
        [{"passed": True, "summary": "command passed"}],
        workers=[
            _source_worker(
                worker_id="worker-docs",
                write_scope=["docs/"],
            ),
            _review_worker(),
        ],
    )
    supervisor = FakeV4Supervisor(
        [
            {"status": "turn_completed", "turn_id": "round-1-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-1-worker-review-review"},
        ],
        event_store=store,
        review_summaries=[
            "<<<CODEX_REVIEW\nverdict: OK\nsummary: patch matches spec and quality bar\nfindings:\n>>>"
        ],
    )

    result = V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=store,
    ).run(
        repo_root=tmp_path,
        goal="Fix source",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "ready_for_codex_accept"
    assert controller.ensured[0]["contract"].write_scope == ["src/"]
    assert supervisor.turns[0]["worker_id"] == "worker-source"


def test_v4_crew_runner_runs_review_before_verification(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    controller = FakeController([{"passed": True, "summary": "command passed"}])
    supervisor = FakeV4Supervisor(
        [
            {"status": "turn_completed", "turn_id": "round-1-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-1-worker-review-review"},
        ],
        event_store=store,
        review_summaries=[
            "<<<CODEX_REVIEW\nverdict: OK\nsummary: patch matches spec and quality bar\nfindings:\n>>>"
        ],
    )

    result = V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=store,
    ).supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
        max_rounds=1,
    )

    assert result["status"] == "ready_for_codex_accept"
    assert controller.verify_called == [
        {"crew_id": "crew-1", "command": "pytest -q", "worker_id": "worker-source"}
    ]
    assert [turn["worker_id"] for turn in supervisor.turns] == ["worker-source", "worker-review"]
    assert "spec" in supervisor.turns[1]["message"].lower()
    assert "code quality" in supervisor.turns[1]["message"].lower()
    assert [event.type for event in store.list_stream("crew-1")] == [
        "worker.patch.recorded",
        "worker.result.recorded",
        "worker.outbox.detected",
        "review.completed",
        "verification.passed",
        "crew.ready_for_accept",
    ]
    assert store.list_stream("crew-1")[3].payload["status"] == "ok"


def test_v4_crew_runner_prefers_typed_review_payload_over_summary_block(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    controller = FakeController([{"passed": True, "summary": "command passed"}])
    supervisor = FakeV4Supervisor(
        [
            {"status": "turn_completed", "turn_id": "round-1-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-1-worker-review-review"},
        ],
        event_store=store,
        review_payloads=[
            {
                "verdict": "warn",
                "summary": "typed review risk",
                "findings": ["typed finding"],
                "evidence_refs": ["review.json"],
            }
        ],
        review_summaries=[
            "<<<CODEX_REVIEW\nverdict: OK\nsummary: stale summary block\nfindings:\n>>>",
        ],
    )

    result = V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=store,
    ).supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
        max_rounds=1,
    )

    review_event = [event for event in store.list_stream("crew-1") if event.type == "review.completed"][0]
    assert result["status"] == "ready_for_codex_accept"
    assert review_event.payload["status"] == "warn"
    assert review_event.payload["summary"] == "typed review risk"
    assert review_event.payload["findings"] == ["typed finding"]


def test_v4_crew_runner_repair_loop_on_blocking_review(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    controller = FakeController([{"passed": True, "summary": "command passed"}])
    supervisor = FakeV4Supervisor(
        [
            {"status": "turn_completed", "turn_id": "round-1-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-1-worker-review-review"},
            {"status": "turn_completed", "turn_id": "round-2-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-2-worker-review-review"},
        ],
        event_store=store,
        review_summaries=[
            "<<<CODEX_REVIEW\nverdict: BLOCK\nsummary: missing regression test\nfindings:\n- Add a regression test.\n>>>",
            "<<<CODEX_REVIEW\nverdict: OK\nsummary: blocker repaired\nfindings:\n>>>",
        ],
    )

    result = V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=store,
    ).supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
        max_rounds=2,
    )

    assert result["status"] == "ready_for_codex_accept"
    assert controller.verify_called == [
        {"crew_id": "crew-1", "command": "pytest -q", "worker_id": "worker-source"}
    ]
    assert controller.challenge_called[0]["summary"].startswith("Review BLOCK: missing regression test")
    assert "missing regression test" in supervisor.turns[2]["message"]
    assert [event.type for event in store.list_stream("crew-1")] == [
        "worker.patch.recorded",
        "worker.result.recorded",
        "worker.outbox.detected",
        "review.completed",
        "challenge.issued",
        "repair.requested",
        "worker.patch.recorded",
        "worker.result.recorded",
        "worker.outbox.detected",
        "review.completed",
        "verification.passed",
        "crew.ready_for_accept",
    ]


def test_v4_crew_runner_creates_learning_feedback_after_repeated_review_blocks(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    controller = FakeController([{"passed": True, "summary": "command passed"}])
    supervisor = FakeV4Supervisor(
        [
            {"status": "turn_completed", "turn_id": "round-1-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-1-worker-review-review"},
            {"status": "turn_completed", "turn_id": "round-2-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-2-worker-review-review"},
        ],
        event_store=store,
        review_summaries=[
            "<<<CODEX_REVIEW\nverdict: BLOCK\nsummary: missing regression test\nfindings:\n- Add a regression test.\n>>>",
            "<<<CODEX_REVIEW\nverdict: BLOCK\nsummary: tests still missing\nfindings:\n- Add the regression test before retrying.\n>>>",
        ],
    )

    result = V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=store,
    ).supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
        max_rounds=2,
    )

    events = store.list_stream("crew-1")
    assert result["status"] == "max_rounds_exhausted"
    assert [event.type for event in events][-3:] == [
        "learning.note_created",
        "guardrail.candidate_created",
        "worker.quality_updated",
    ]
    quality = [event for event in events if event.type == "worker.quality_updated"][0]
    assert quality.worker_id == "worker-source"
    assert quality.payload["score_delta"] == -2
    assert quality.payload["reason_codes"] == ["repeated_review_block"]


def test_v4_crew_runner_creates_learning_feedback_after_repeated_verification_failures(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    controller = FakeController(
        [
            {"passed": False, "summary": "unit tests failed"},
            {"passed": False, "summary": "unit tests still failed"},
        ]
    )
    supervisor = FakeV4Supervisor(
        [
            {"status": "turn_completed", "turn_id": "round-1-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-1-worker-review-review"},
            {"status": "turn_completed", "turn_id": "round-2-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-2-worker-review-review"},
        ],
        event_store=store,
        review_summaries=[
            "<<<CODEX_REVIEW\nverdict: OK\nsummary: patch is reviewable\nfindings:\n>>>",
            "<<<CODEX_REVIEW\nverdict: OK\nsummary: patch is still reviewable\nfindings:\n>>>",
        ],
    )

    result = V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=store,
    ).supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
        max_rounds=2,
    )

    events = store.list_stream("crew-1")
    assert result["status"] == "max_rounds_exhausted"
    assert [event.type for event in events][-3:] == [
        "learning.note_created",
        "guardrail.candidate_created",
        "worker.quality_updated",
    ]
    quality = [event for event in events if event.type == "worker.quality_updated"][0]
    assert quality.worker_id == "worker-source"
    assert quality.payload["score_delta"] == -3
    assert quality.payload["reason_codes"] == ["repeated_verification_failed"]


def test_v4_crew_runner_waits_without_recording_changes_when_turn_is_not_terminal(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    controller = FakeController([{"passed": True, "summary": "command passed"}])
    supervisor = FakeV4Supervisor(
        [{"status": "waiting", "turn_id": "round-1-worker-source-source", "reason": "missing_outbox"}]
    )

    result = V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=store,
    ).supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
        max_rounds=1,
    )

    assert result["status"] == "waiting_for_worker"
    assert result["reason"] == "missing_outbox"
    assert controller.changes_called == []
    assert controller.verify_called == []
    assert store.list_stream("crew-1") == []


def test_v4_crew_runner_dynamic_run_starts_crew_and_spawns_source_worker(
    tmp_path: Path,
) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    controller = FakeController([{"passed": True, "summary": "command passed"}], workers=[])
    supervisor = FakeV4Supervisor(
        [
            {"status": "turn_completed", "turn_id": "round-1-worker-source-source"},
            {"status": "turn_completed", "turn_id": "round-1-worker-review-review"},
        ],
        event_store=store,
        review_summaries=[
            "<<<CODEX_REVIEW\nverdict: OK\nsummary: patch matches spec and quality bar\nfindings:\n>>>"
        ],
    )

    result = V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=store,
    ).run(
        repo_root=tmp_path,
        goal="Fix tests",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "ready_for_codex_accept"
    assert controller.started == [{"dynamic": True, "repo_root": tmp_path, "goal": "Fix tests"}]
    assert controller.ensured[0]["contract"].authority_level.value == "source_write"
    assert supervisor.turns[0]["message"].startswith("Begin or continue")


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


class FakeCrew:
    crew_id = "crew-1"


class FakeController:
    def __init__(self, verification_results: list[dict], workers: list[dict] | None = None) -> None:
        self.verification_results = list(verification_results)
        self.started = []
        self.ensured = []
        self.changes_called = []
        self.verify_called = []
        self.challenge_called = []
        self.claimed = []
        self.released = []
        self.repo_root = None
        self.status_payload = {
            "crew": {"crew_id": "crew-1", "root_goal": "Fix tests"},
            "workers": workers if workers is not None else [_source_worker(), _review_worker()],
            "worker_contracts": [],
        }

    def start(self, **kwargs):
        self.started.append(kwargs)
        return FakeCrew()

    def start_dynamic(self, **kwargs):
        self.started.append({"dynamic": True, **kwargs})
        return FakeCrew()

    def status(self, **kwargs):
        self.repo_root = kwargs.get("repo_root")
        return self.status_payload

    def ensure_worker(self, **kwargs):
        self.ensured.append(kwargs)
        if kwargs["contract"].label == "patch-risk-auditor":
            worker = _review_worker(contract_id=kwargs["contract"].contract_id)
        else:
            worker = _source_worker(contract_id=kwargs["contract"].contract_id)
        self.status_payload["workers"].append(worker)
        return worker

    def changes(self, **kwargs):
        self.changes_called.append(kwargs)
        diff_artifact = f"workers/{kwargs['worker_id']}/diff.patch"
        artifact = f"workers/{kwargs['worker_id']}/changes.json"
        if self.repo_root is not None:
            artifact_root = Path(self.repo_root) / ".orchestrator" / "crews" / kwargs["crew_id"] / "artifacts"
            diff_path = artifact_root / diff_artifact
            diff_path.parent.mkdir(parents=True, exist_ok=True)
            diff_path.write_text(_patch_for("src/app.py"), encoding="utf-8")
        return {
            "worker_id": kwargs["worker_id"],
            "base_ref": "base-sha",
            "changed_files": ["src/app.py"],
            "artifact": artifact,
            "diff_artifact": diff_artifact,
        }

    def verify(self, **kwargs):
        self.verify_called.append(kwargs)
        return self.verification_results.pop(0)

    def challenge(self, **kwargs):
        self.challenge_called.append(kwargs)
        return {"summary": kwargs["summary"]}

    def claim_worker(self, crew_id, worker_id):
        self.claimed.append((crew_id, worker_id))

    def release_worker(self, crew_id, worker_id):
        self.released.append((crew_id, worker_id))


class FakeV4Supervisor:
    def __init__(
        self,
        results: list[dict],
        *,
        event_store: SQLiteEventStore | None = None,
        review_summaries: list[str] | None = None,
        review_payloads: list[dict] | None = None,
    ) -> None:
        self.results = list(results)
        self.event_store = event_store
        self.review_summaries = list(review_summaries or [])
        self.review_payloads = list(review_payloads or [])
        self.registered = []
        self.turns = []

    def register_worker(self, spec):
        self.registered.append(spec)

    def run_source_turn(self, **kwargs):
        self.turns.append(kwargs)
        return self.results.pop(0)

    def run_worker_turn(self, **kwargs):
        self.turns.append(kwargs)
        result = self.results.pop(0)
        if kwargs.get("phase") == "review" and self.event_store is not None:
            summary = self.review_summaries.pop(0)
            review = self.review_payloads.pop(0) if self.review_payloads else None
            self.event_store.append(
                stream_id=kwargs["crew_id"],
                type="worker.outbox.detected",
                crew_id=kwargs["crew_id"],
                worker_id=kwargs["worker_id"],
                turn_id=result["turn_id"],
                round_id=kwargs["round_id"],
                contract_id=kwargs.get("contract_id", ""),
                payload={
                    "valid": True,
                    "status": "completed",
                    "summary": summary,
                    **({"review": review} if review is not None else {}),
                },
            )
        return result


def _source_worker(
    *,
    contract_id: str = "source_write",
    worker_id: str = "worker-source",
    write_scope: list[str] | None = None,
) -> dict:
    return {
        "worker_id": worker_id,
        "role": WorkerRole.IMPLEMENTER.value,
        "label": "targeted-code-editor",
        "contract_id": contract_id,
        "capabilities": ["edit_source", "edit_tests", "run_verification"],
        "authority_level": "source_write",
        "write_scope": write_scope or ["src/", "tests/"],
        "workspace_path": f"/tmp/{worker_id}",
        "terminal_pane": f"crew-{worker_id}:claude.0",
        "transcript_artifact": f"workers/{worker_id}/transcript.txt",
    }


def _review_worker(*, contract_id: str = "patch_auditor") -> dict:
    return {
        "worker_id": "worker-review",
        "role": WorkerRole.REVIEWER.value,
        "label": "patch-risk-auditor",
        "contract_id": contract_id,
        "capabilities": ["review_patch", "inspect_code"],
        "authority_level": "readonly",
        "write_scope": [],
        "workspace_path": "/tmp/worker-review",
        "terminal_pane": "crew-worker-review:claude.0",
        "transcript_artifact": "workers/worker-review/transcript.txt",
    }


def _patch_for(path: str) -> str:
    return "\n".join(
        [
            f"diff --git a/{path} b/{path}",
            "index e69de29..4b825dc 100644",
            f"--- a/{path}",
            f"+++ b/{path}",
            "@@ -0,0 +1 @@",
            "+hello",
            "",
        ]
    )
