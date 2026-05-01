from pathlib import Path

import pytest

from codex_claude_orchestrator.crew.supervisor_loop import CrewSupervisorLoop


class FakeCrew:
    crew_id = "crew-1"


class FakeController:
    def __init__(self, verification_results):
        self.verification_results = list(verification_results)
        self.sent = []
        self.observed = []
        self.changes_called = []
        self.verify_called = []
        self.challenge_called = []
        self.ensured = []
        self.snapshots = []
        self.known_pitfalls = []
        self.decisions = []
        self.started = []
        self.artifacts = []
        self.blackboard_entries = []
        self.status_payload = {
            "crew": {"crew_id": "crew-1"},
            "workers": [
                {"worker_id": "worker-explorer", "role": "explorer"},
                {"worker_id": "worker-implementer", "role": "implementer"},
                {"worker_id": "worker-reviewer", "role": "reviewer"},
            ],
        }

    def start(self, **kwargs):
        self.started.append(kwargs)
        return FakeCrew()

    def start_dynamic(self, **kwargs):
        self.started.append({"dynamic": True, **kwargs})
        return FakeCrew()

    def status(self, **kwargs):
        return self.status_payload

    def observe_worker(self, **kwargs):
        self.observed.append(kwargs)
        marker = kwargs.get("turn_marker") or "<<<CODEX_TURN_DONE status=ready_for_codex>>>"
        return {"snapshot": f"{kwargs['worker_id']} done\n{marker}", "marker_seen": True}

    def send_worker(self, **kwargs):
        self.sent.append(kwargs)
        return {"marker_seen": False}

    def changes(self, **kwargs):
        self.changes_called.append(kwargs)
        return {
            "worker_id": kwargs["worker_id"],
            "changed_files": ["src/app.py"],
            "diff_artifact": "workers/worker-implementer/diff.patch",
        }

    def verify(self, **kwargs):
        self.verify_called.append(kwargs)
        return self.verification_results.pop(0)

    def challenge(self, **kwargs):
        self.challenge_called.append(kwargs)
        return {"type": "risk", "content": kwargs["summary"]}

    def ensure_worker(self, **kwargs):
        self.ensured.append(kwargs)
        worker_id = "worker-source" if kwargs["contract"].label == "targeted-code-editor" else f"worker-{kwargs['contract'].label}"
        worker = {
            "worker_id": worker_id,
            "role": "implementer" if kwargs["contract"].authority_level.value != "readonly" else "reviewer",
            "label": kwargs["contract"].label,
            "contract_id": kwargs["contract"].contract_id,
            "capabilities": kwargs["contract"].required_capabilities,
            "authority_level": kwargs["contract"].authority_level.value,
            "write_scope": kwargs["contract"].write_scope,
        }
        self.status_payload["workers"] = [*self.status_payload.get("workers", []), worker]
        return worker

    def write_team_snapshot(self, **kwargs):
        self.snapshots.append(kwargs)
        return {"crew_id": kwargs["crew_id"], "last_decision": kwargs.get("last_decision", {})}

    def append_known_pitfall(self, **kwargs):
        self.known_pitfalls.append(kwargs)
        return kwargs

    def record_decision(self, **kwargs):
        self.decisions.append(kwargs["action"])
        return kwargs["action"]

    def write_json_artifact(self, **kwargs):
        self.artifacts.append(kwargs)
        return kwargs["artifact_name"]

    def record_blackboard_entry(self, **kwargs):
        self.blackboard_entries.append(kwargs)
        return {
            "type": kwargs["entry_type"].value if hasattr(kwargs["entry_type"], "value") else kwargs["entry_type"],
            "content": kwargs["content"],
            "evidence_refs": kwargs.get("evidence_refs", []),
        }


def test_supervisor_loop_records_changes_reviews_and_returns_ready_when_verification_passes(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
        max_rounds=1,
    )

    assert result["status"] == "ready_for_codex_accept"
    assert controller.changes_called[0]["worker_id"] == "worker-implementer"
    assert controller.verify_called[0]["worker_id"] == "worker-implementer"
    assert any(call["worker_id"] == "worker-reviewer" for call in controller.sent)
    implementer_send = next(call for call in controller.sent if call["worker_id"] == "worker-implementer")
    implementer_observe = next(
        call for call in controller.observed if call["worker_id"] == "worker-implementer" and call.get("turn_marker")
    )
    assert implementer_observe["turn_marker"] == implementer_send["turn_marker"]


def test_supervisor_loop_challenges_implementer_after_failed_verification_then_retries(tmp_path: Path):
    controller = FakeController(
        [
            {"passed": False, "summary": "command failed: exit code 1"},
            {"passed": True, "summary": "command passed: exit code 0"},
        ]
    )
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
        max_rounds=2,
    )

    assert result["status"] == "ready_for_codex_accept"
    assert controller.challenge_called[0]["summary"] == "command failed: exit code 1"
    assert any("Fix verification failure" in call["message"] for call in controller.sent)


def test_supervisor_loop_run_starts_crew_then_supervises(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Build V3 MVP",
        verification_commands=["pytest -q"],
        max_rounds=1,
        allow_dirty_base=True,
    )

    assert result["crew_id"] == "crew-1"
    assert result["status"] == "ready_for_codex_accept"
    assert controller.started[0]["goal"] == "Build V3 MVP"
    assert controller.started[0]["allow_dirty_base"] is True


def test_supervisor_loop_requires_verification_commands(tmp_path: Path):
    controller = FakeController([])
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    with pytest.raises(ValueError, match="at least one verification command"):
        loop.supervise(repo_root=tmp_path, crew_id="crew-1", verification_commands=[], max_rounds=1)


def test_supervisor_loop_dynamic_run_spawns_source_contract_without_static_roster(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Fix tests"}, "workers": []}
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Fix tests",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "ready_for_codex_accept"
    assert controller.started[0]["dynamic"] is True
    assert controller.started[0]["goal"] == "Fix tests"
    assert controller.ensured[0]["contract"].label == "targeted-code-editor"
    assert controller.sent[0]["worker_id"] == "worker-source"
    assert controller.verify_called[0]["worker_id"] == "worker-source"
    assert controller.snapshots[-1]["last_decision"]["action_type"] == "accept_ready"


def test_supervisor_loop_dynamic_ignores_legacy_implementer_without_source_write_authority(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {
        "crew": {"crew_id": "crew-1", "root_goal": "Fix source"},
        "workers": [{"worker_id": "worker-legacy", "role": "implementer"}],
    }
    controller.changes = lambda **kwargs: {"worker_id": kwargs["worker_id"], "changed_files": []}
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Fix source",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "ready_for_codex_accept"
    assert controller.ensured[0]["contract"].label == "targeted-code-editor"
    assert all(call["worker_id"] != "worker-legacy" for call in controller.sent)
    assert all(call["worker_id"] != "worker-legacy" for call in controller.verify_called)


def test_supervisor_loop_dynamic_reselects_source_worker_when_cached_worker_stops(tmp_path: Path):
    controller = FakeController(
        [
            {"passed": False, "summary": "pytest failed"},
            {"passed": True, "summary": "command passed: exit code 0"},
        ]
    )
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Fix source"}, "workers": []}
    worker_ids = iter(["worker-source-1", "worker-source-2"])

    def ensure_numbered_worker(**kwargs):
        controller.ensured.append(kwargs)
        worker = {
            "worker_id": next(worker_ids),
            "role": "implementer",
            "label": kwargs["contract"].label,
            "contract_id": kwargs["contract"].contract_id,
            "capabilities": kwargs["contract"].required_capabilities,
            "authority_level": kwargs["contract"].authority_level.value,
            "write_scope": kwargs["contract"].write_scope,
        }
        controller.status_payload["workers"] = [
            item
            for item in controller.status_payload.get("workers", [])
            if item["worker_id"] != worker["worker_id"]
        ]
        controller.status_payload["workers"].append(worker)
        return worker

    def verify_and_stop_first_worker(**kwargs):
        controller.verify_called.append(kwargs)
        result = controller.verification_results.pop(0)
        if kwargs["worker_id"] == "worker-source-1":
            controller.status_payload["workers"] = [
                {**item, "status": "stopped"} if item["worker_id"] == "worker-source-1" else item
                for item in controller.status_payload["workers"]
            ]
        return result

    controller.ensure_worker = ensure_numbered_worker
    controller.verify = verify_and_stop_first_worker
    controller.changes = lambda **kwargs: {"worker_id": kwargs["worker_id"], "changed_files": []}
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Fix source",
        verification_commands=["pytest -q"],
        max_rounds=2,
        spawn_policy="dynamic",
    )

    assert result["status"] == "ready_for_codex_accept"
    assert [call["worker_id"] for call in controller.sent if call["message"].startswith("Begin or continue")] == [
        "worker-source-1",
        "worker-source-2",
    ]
    assert [call["worker_id"] for call in controller.verify_called] == ["worker-source-1", "worker-source-2"]


def test_supervisor_loop_dynamic_infers_tools_write_scope_from_repo_layout(tmp_path: Path):
    (tmp_path / "tools" / "tests").mkdir(parents=True)
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Fix tools tests"}, "workers": []}

    def tools_changes(**kwargs):
        controller.changes_called.append(kwargs)
        return {
            "worker_id": kwargs["worker_id"],
            "changed_files": ["tools/app.py"],
            "diff_artifact": "workers/worker-source/diff.patch",
        }

    controller.changes = tools_changes
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Fix tools tests",
        verification_commands=["python -m pytest tools/tests -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "ready_for_codex_accept"
    assert controller.ensured[0]["contract"].write_scope == ["tools/", "tools/tests/"]


def test_supervisor_loop_dynamic_seed_context_scout_runs_readonly_scout_before_source_worker(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Implement risky architecture change"}, "workers": []}
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Implement risky architecture change",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
        seed_contract="context_scout",
    )

    spawned_labels = [call["contract"].label for call in controller.ensured]
    assert result["status"] == "ready_for_codex_accept"
    assert spawned_labels[:2] == ["repo-context-scout", "targeted-code-editor"]
    assert controller.sent[0]["worker_id"] == "worker-repo-context-scout"
    assert controller.sent[1]["worker_id"] == "worker-source"
    assert any(event.get("label") == "repo-context-scout" for event in result["events"])


def test_supervisor_loop_dynamic_spawns_patch_auditor_after_source_patch(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Refactor public API"}, "workers": []}
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Refactor public API",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    spawned_labels = [call["contract"].label for call in controller.ensured]
    assert result["status"] == "ready_for_codex_accept"
    assert spawned_labels == ["targeted-code-editor", "patch-risk-auditor"]
    assert any(call["worker_id"] == "worker-patch-risk-auditor" for call in controller.sent)
    assert any(event.get("label") == "patch-risk-auditor" for event in result["events"])


def test_supervisor_loop_dynamic_records_decisions_for_spawn_review_and_accept(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Refactor public API"}, "workers": []}
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Refactor public API",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "ready_for_codex_accept"
    assert [decision["action_type"] for decision in controller.decisions] == [
        "spawn_worker",
        "spawn_worker",
        "accept_ready",
    ]
    assert controller.decisions[0]["contract"]["label"] == "targeted-code-editor"
    assert controller.decisions[1]["contract"]["label"] == "patch-risk-auditor"


def test_supervisor_loop_dynamic_spawns_browser_tester_for_ui_goal_after_review(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Update browser UI checkout flow"}, "workers": []}
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Update browser UI checkout flow",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    spawned_labels = [call["contract"].label for call in controller.ensured]
    assert result["status"] == "ready_for_codex_accept"
    assert spawned_labels == ["targeted-code-editor", "patch-risk-auditor", "browser-flow-tester"]
    assert any(call["worker_id"] == "worker-browser-flow-tester" for call in controller.sent)
    assert any(event.get("label") == "browser-flow-tester" for event in result["events"])


def test_supervisor_loop_dynamic_records_known_pitfall_and_spawns_guardrail_after_three_failures(tmp_path: Path):
    controller = FakeController(
        [
            {"passed": False, "summary": "pytest failed 1"},
            {"passed": False, "summary": "pytest failed 2"},
            {"passed": False, "summary": "pytest failed 3"},
        ]
    )
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Fix repeated failures"}, "workers": []}
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Fix repeated failures",
        verification_commands=["pytest -q"],
        max_rounds=3,
        spawn_policy="dynamic",
    )

    spawned_labels = [call["contract"].label for call in controller.ensured]
    assert result["status"] == "max_rounds_exhausted"
    assert "verification-failure-analyst" in spawned_labels
    assert "guardrail-maintainer" in spawned_labels
    assert controller.known_pitfalls[0]["failure_class"] == "verification_repeat"
    assert "pytest failed 3" in controller.known_pitfalls[0]["summary"]


def test_supervisor_loop_dynamic_challenges_out_of_scope_low_risk_change(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Fix source"}, "workers": []}

    def out_of_scope_changes(**kwargs):
        controller.changes_called.append(kwargs)
        return {
            "worker_id": kwargs["worker_id"],
            "changed_files": ["docs/notes.md"],
            "artifact": "workers/worker-source/changes.json",
            "diff_artifact": "workers/worker-source/diff.patch",
        }

    controller.changes = out_of_scope_changes
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Fix source",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "max_rounds_exhausted"
    assert controller.verify_called == []
    assert controller.challenge_called[0]["summary"].startswith("Changed files outside write_scope")
    assert any(item["artifact_name"] == "gates/round-1/write_scope.json" for item in controller.artifacts)


def test_supervisor_loop_dynamic_blocks_protected_scope_violation(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Fix source"}, "workers": []}

    def protected_changes(**kwargs):
        controller.changes_called.append(kwargs)
        return {
            "worker_id": kwargs["worker_id"],
            "changed_files": ["pyproject.toml"],
            "artifact": "workers/worker-source/changes.json",
            "diff_artifact": "workers/worker-source/diff.patch",
        }

    controller.changes = protected_changes
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Fix source",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "needs_human"
    assert result["reason"] == "write_scope_blocked"
    assert result["readiness_artifact"] == "readiness/round-1.json"
    assert controller.verify_called == []
