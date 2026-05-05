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
        if kwargs["worker_id"] == "worker-patch-risk-auditor":
            return {
                "snapshot": "\n".join(
                    [
                        f"{kwargs['worker_id']} done",
                        "<<<CODEX_REVIEW",
                        "verdict: OK",
                        "summary: Patch looks safe.",
                        "findings:",
                        "- Tests cover the changed path.",
                        ">>>",
                        marker,
                    ]
                ),
                "marker_seen": True,
            }
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


def test_supervisor_loop_requires_verification_commands(tmp_path: Path):
    controller = FakeController([])
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    with pytest.raises(ValueError, match="at least one verification command"):
        loop.supervise(repo_root=tmp_path, crew_id="crew-1", verification_commands=[], max_rounds=1)


def test_supervisor_loop_static_explorer_without_expected_marker_keeps_waiting(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])

    def observe_without_marker(**kwargs):
        controller.observed.append(kwargs)
        return {"snapshot": "still working", "marker_seen": False}

    controller.observe_worker = observe_without_marker
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.supervise(
        repo_root=tmp_path,
        crew_id="crew-1",
        verification_commands=["pytest -q"],
        max_rounds=1,
    )

    assert result["status"] == "waiting_for_worker"
    assert result["worker_id"] == "worker-explorer"
    assert result["reason"] == "expected marker not found"
    assert controller.sent == []
