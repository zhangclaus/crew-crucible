from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path

from codex_claude_orchestrator.cli import main
from codex_claude_orchestrator.models import EvaluationOutcome, NextAction, RunRecord, TaskRecord, WorkspaceMode
from codex_claude_orchestrator.run_recorder import RunRecorder


def test_build_parser_exposes_dispatch_subcommand():
    from codex_claude_orchestrator.cli import build_parser

    parser = build_parser()
    subparsers_action = next(action for action in parser._actions if action.dest == "command")
    assert "dispatch" in subparsers_action.choices


class FakeSupervisor:
    def dispatch(self, task, source_repo):
        return EvaluationOutcome(
            accepted=True,
            next_action=NextAction.ACCEPT,
            summary=f"accepted {task.goal}",
        )


def test_main_dispatch_prints_json_summary(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    monkeypatch.setattr(
        "codex_claude_orchestrator.cli.build_supervisor",
        lambda state_root: FakeSupervisor(),
    )

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "dispatch",
                "--task-id",
                "task-cli",
                "--goal",
                "Inspect the repository",
                "--repo",
                str(repo_root),
                "--workspace-mode",
                "readonly",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["accepted"] is True
    assert payload["summary"] == "accepted Inspect the repository"


def test_agents_list_prints_configured_profiles():
    stdout = StringIO()

    with redirect_stdout(stdout):
        exit_code = main(["agents", "list"])

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["agents"][0]["name"] == "claude"
    assert payload["agents"][0]["adapter"] == "claude-cli"


def test_doctor_reports_python_and_claude_checks():
    stdout = StringIO()

    with redirect_stdout(stdout):
        exit_code = main(["doctor"])

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["python"]["ok"] is True
    assert "claude_cli" in payload


def test_runs_list_prints_recorded_run_summaries(tmp_path: Path):
    repo_root = tmp_path / "repo"
    recorder = RunRecorder(repo_root / ".orchestrator")
    task = TaskRecord(
        task_id="task-cli-run",
        parent_task_id=None,
        origin="cli",
        assigned_agent="claude",
        goal="List this run",
        task_type="review",
        scope=str(repo_root),
        workspace_mode=WorkspaceMode.READONLY,
    )
    run = RunRecord(
        run_id="run-cli-list",
        task_id=task.task_id,
        agent="claude",
        adapter="claude-cli",
        workspace_id="workspace-cli",
    )
    recorder.start_run(run, task)
    recorder.write_result(
        run.run_id,
        result=FakeWorkerResult(summary="listed"),
        evaluation=EvaluationOutcome(
            accepted=True,
            next_action=NextAction.ACCEPT,
            summary="listed",
        ),
    )

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["runs", "list", "--repo", str(repo_root)])

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["runs"][0]["run_id"] == "run-cli-list"
    assert payload["runs"][0]["summary"] == "listed"


def test_runs_show_prints_recorded_run_details(tmp_path: Path):
    repo_root = tmp_path / "repo"
    recorder = RunRecorder(repo_root / ".orchestrator")
    task = TaskRecord(
        task_id="task-cli-show",
        parent_task_id=None,
        origin="cli",
        assigned_agent="claude",
        goal="Show this run",
        task_type="review",
        scope=str(repo_root),
        workspace_mode=WorkspaceMode.READONLY,
    )
    run = RunRecord(
        run_id="run-cli-show",
        task_id=task.task_id,
        agent="claude",
        adapter="claude-cli",
        workspace_id="workspace-cli",
    )
    recorder.start_run(run, task)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["runs", "show", "--repo", str(repo_root), "--run-id", run.run_id])

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["run"]["run_id"] == "run-cli-show"
    assert payload["task"]["goal"] == "Show this run"
    assert "artifacts" in payload


class FakeWorkerResult:
    def __init__(self, summary: str):
        self.raw_output = f'{{"summary":"{summary}"}}'
        self.stdout = self.raw_output
        self.stderr = ""
        self.exit_code = 0
        self.structured_output = {"summary": summary}
        self.changed_files = []
        self.parse_error = None

    def to_dict(self):
        return {
            "raw_output": self.raw_output,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "structured_output": self.structured_output,
            "changed_files": self.changed_files,
            "parse_error": self.parse_error,
        }
