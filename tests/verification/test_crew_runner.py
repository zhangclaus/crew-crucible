from pathlib import Path
from subprocess import CompletedProcess

from codex_claude_orchestrator.crew.models import CrewRecord
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.verification.crew_runner import CrewVerificationRunner
from codex_claude_orchestrator.core.policy_gate import PolicyGate


def test_crew_verification_records_command_artifacts_and_blackboard_entry(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    recorder.start_crew(CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo=repo_root))

    def fake_runner(argv, **kwargs):
        return CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    result = CrewVerificationRunner(
        repo_root=repo_root,
        recorder=recorder,
        policy_gate=PolicyGate(),
        runner=fake_runner,
        verification_id_factory=lambda: "verification-1",
        entry_id_factory=lambda: "entry-verification",
    ).run("crew-1", "pytest -q")

    details = recorder.read_crew("crew-1")
    assert result["passed"] is True
    assert result["summary"] == "command passed: exit code 0"
    assert details["blackboard"][0]["type"] == "verification"
    assert "verification/verification-1/stdout.txt" in details["artifacts"]


def test_crew_verification_runs_command_in_target_cwd_and_records_target_worker(tmp_path: Path):
    repo_root = tmp_path / "repo"
    worktree = tmp_path / "worker-worktree"
    repo_root.mkdir()
    worktree.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    recorder.start_crew(CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo=repo_root))
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    result = CrewVerificationRunner(
        repo_root=repo_root,
        recorder=recorder,
        policy_gate=PolicyGate(),
        runner=fake_runner,
        verification_id_factory=lambda: "verification-worker",
        entry_id_factory=lambda: "entry-verification",
    ).run("crew-1", "pytest -q", cwd=worktree, target_worker_id="worker-implementer")

    assert calls[0]["cwd"] == worktree
    assert result["cwd"] == str(worktree)
    assert result["target_worker_id"] == "worker-implementer"


def test_crew_verification_resolves_repo_relative_executable_when_missing_from_worker_cwd(tmp_path: Path):
    repo_root = tmp_path / "repo"
    worktree = tmp_path / "worker-worktree"
    repo_python = repo_root / ".venv" / "bin" / "python"
    repo_python.parent.mkdir(parents=True)
    repo_python.write_text("#!/bin/sh\n", encoding="utf-8")
    worktree.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    recorder.start_crew(CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo=repo_root))
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    CrewVerificationRunner(
        repo_root=repo_root,
        recorder=recorder,
        policy_gate=PolicyGate(),
        runner=fake_runner,
        verification_id_factory=lambda: "verification-worker",
        entry_id_factory=lambda: "entry-verification",
    ).run(
        "crew-1",
        ".venv/bin/python -m pytest tools/tests -q",
        cwd=worktree,
        target_worker_id="worker-implementer",
    )

    assert calls[0]["argv"][0] == str(repo_python)
    assert calls[0]["argv"][1:] == ["-m", "pytest", "tools/tests", "-q"]
