from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from subprocess import CompletedProcess

from codex_claude_orchestrator.cli import main
from codex_claude_orchestrator.models import (
    EvaluationOutcome,
    LearningNote,
    NextAction,
    RunRecord,
    SessionRecord,
    SessionStatus,
    TaskRecord,
    WorkspaceMode,
)
from codex_claude_orchestrator.run_recorder import RunRecorder
from codex_claude_orchestrator.session_recorder import SessionRecorder
from codex_claude_orchestrator.skill_evolution import SkillEvolution


def test_build_parser_exposes_dispatch_subcommand():
    from codex_claude_orchestrator.cli import build_parser

    parser = build_parser()
    subparsers_action = next(action for action in parser._actions if action.dest == "command")
    assert "dispatch" in subparsers_action.choices


def test_build_parser_exposes_v2_session_and_skill_commands():
    from codex_claude_orchestrator.cli import build_parser

    parser = build_parser()
    subparsers_action = next(action for action in parser._actions if action.dest == "command")

    assert "session" in subparsers_action.choices
    assert "sessions" in subparsers_action.choices
    assert "skills" in subparsers_action.choices
    assert "ui" in subparsers_action.choices
    assert "term" in subparsers_action.choices
    assert "claude" in subparsers_action.choices


class FakeSupervisor:
    def dispatch(self, task, source_repo):
        return EvaluationOutcome(
            accepted=True,
            next_action=NextAction.ACCEPT,
            summary=f"accepted {task.goal}",
        )


class FakeSessionEngine:
    def __init__(self):
        self.calls = []

    def start(self, **kwargs):
        self.calls.append(kwargs)
        return SessionRecord(
            session_id="session-cli",
            root_task_id="task-session-cli",
            repo=str(kwargs["repo_root"]),
            goal=kwargs["goal"],
            assigned_agent=kwargs["assigned_agent"],
            status=SessionStatus.ACCEPTED,
            workspace_mode=kwargs["workspace_mode"],
            max_rounds=kwargs["max_rounds"],
            verification_commands=kwargs["verification_commands"],
            final_summary="session accepted",
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


def test_main_session_start_prints_json_summary(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_engine = FakeSessionEngine()

    monkeypatch.setattr(
        "codex_claude_orchestrator.cli.build_session_engine",
        lambda repo_root: fake_engine,
    )

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "session",
                "start",
                "--goal",
                "Implement V2",
                "--repo",
                str(repo_root),
                "--workspace-mode",
                "isolated",
                "--max-rounds",
                "2",
                "--verification-command",
                "pytest -q",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["session_id"] == "session-cli"
    assert payload["status"] == "accepted"
    assert payload["final_summary"] == "session accepted"
    assert fake_engine.calls[0]["goal"] == "Implement V2"
    assert fake_engine.calls[0]["max_rounds"] == 2
    assert fake_engine.calls[0]["verification_commands"] == ["pytest -q"]


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


def test_sessions_list_and_show_print_recorded_session_details(tmp_path: Path):
    repo_root = tmp_path / "repo"
    recorder = SessionRecorder(repo_root / ".orchestrator")
    session = SessionRecord(
        session_id="session-cli-list",
        root_task_id="task-session-cli",
        repo=str(repo_root),
        goal="Show session",
        assigned_agent="claude",
    )
    recorder.start_session(session)
    recorder.finalize_session(session.session_id, SessionStatus.ACCEPTED, "accepted")

    stdout = StringIO()
    with redirect_stdout(stdout):
        list_exit = main(["sessions", "list", "--repo", str(repo_root)])
    list_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        show_exit = main(["sessions", "show", "--repo", str(repo_root), "--session-id", session.session_id])
    show_payload = json.loads(stdout.getvalue())

    assert list_exit == 0
    assert list_payload["sessions"][0]["session_id"] == "session-cli-list"
    assert list_payload["sessions"][0]["summary"] == "accepted"
    assert show_exit == 0
    assert show_payload["session"]["session_id"] == "session-cli-list"
    assert show_payload["final_report"]["status"] == "accepted"


def test_skills_lifecycle_commands_print_json(tmp_path: Path):
    repo_root = tmp_path / "repo"
    evolution = SkillEvolution(repo_root / ".orchestrator")
    record = evolution.create_pending_skill(
        LearningNote(
            note_id="learning-cli",
            session_id="session-cli",
            challenge_ids=["challenge-cli"],
            summary="Require verification before completion.",
            proposed_skill_name="Verification Discipline",
            trigger_conditions=["session retry"],
            evidence_summary="A challenge required stronger verification.",
            confidence=0.7,
        )
    )

    stdout = StringIO()
    with redirect_stdout(stdout):
        list_exit = main(["skills", "list", "--repo", str(repo_root)])
    list_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        show_exit = main(["skills", "show", "--repo", str(repo_root), "--skill-id", record.name])
    show_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        approve_exit = main(["skills", "approve", "--repo", str(repo_root), "--skill-id", record.name])
    approve_payload = json.loads(stdout.getvalue())

    assert list_exit == 0
    assert list_payload["skills"][0]["name"] == "verification-discipline"
    assert list_payload["skills"][0]["status"] == "pending"
    assert show_exit == 0
    assert show_payload["record"]["name"] == "verification-discipline"
    assert "## Verification" in show_payload["skill"]
    assert approve_exit == 0
    assert approve_payload["status"] == "active"


def test_skills_reject_command_prints_json(tmp_path: Path):
    repo_root = tmp_path / "repo"
    evolution = SkillEvolution(repo_root / ".orchestrator")
    record = evolution.create_pending_skill(
        LearningNote(
            note_id="learning-reject",
            session_id="session-cli",
            challenge_ids=["challenge-cli"],
            summary="Too broad.",
            proposed_skill_name="Too Broad",
        )
    )

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "skills",
                "reject",
                "--repo",
                str(repo_root),
                "--skill-id",
                record.name,
                "--reason",
                "too broad",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["status"] == "rejected"


def test_ui_command_starts_visual_console(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls = []

    def fake_run_ui_server(**kwargs):
        calls.append(kwargs)
        return {"url": "http://127.0.0.1:9999", "repo": str(kwargs["repo_root"])}

    monkeypatch.setattr("codex_claude_orchestrator.cli.run_ui_server", fake_run_ui_server)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["ui", "--repo", str(repo_root), "--host", "127.0.0.1", "--port", "9999"])

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["url"] == "http://127.0.0.1:9999"
    assert calls[0]["repo_root"] == repo_root.resolve()
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 9999


def test_claude_open_launches_direct_window(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_launcher = FakeClaudeWindowLauncher()

    monkeypatch.setattr("codex_claude_orchestrator.cli.build_claude_window_launcher", lambda: fake_launcher)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "claude",
                "open",
                "--repo",
                str(repo_root),
                "--goal",
                "Inspect repo",
                "--workspace-mode",
                "readonly",
                "--dry-run",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["run_id"] == "claude-open-test"
    assert payload["launched"] is False
    assert fake_launcher.calls[0] == {
        "repo_root": repo_root.resolve(),
        "goal": "Inspect repo",
        "workspace_mode": "readonly",
        "terminal_app": "terminal",
        "dry_run": True,
    }


def test_term_session_start_launches_tmux_console(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_console = FakeTmuxConsole()

    monkeypatch.setattr("codex_claude_orchestrator.cli.build_tmux_console", lambda: fake_console)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "term",
                "session",
                "start",
                "--name",
                "orchestrator-test",
                "--goal",
                "Inspect repo",
                "--repo",
                str(repo_root),
                "--workspace-mode",
                "readonly",
                "--verification-command",
                "pytest -q",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["tmux_session"] == "orchestrator-test"
    assert payload["attach_command"] == "tmux attach -t orchestrator-test"
    assert fake_console.launch_calls[0]["name"] == "orchestrator-test"
    assert fake_console.launch_calls[0]["repo_root"] == repo_root.resolve()
    assert fake_console.launch_calls[0]["session_args"] == [
        "--goal",
        "Inspect repo",
        "--repo",
        str(repo_root.resolve()),
        "--workspace-mode",
        "readonly",
        "--assigned-agent",
        "claude",
        "--max-rounds",
        "1",
        "--verification-command",
        "pytest -q",
    ]


def test_term_list_and_attach_use_tmux_console(monkeypatch):
    fake_console = FakeTmuxConsole()

    monkeypatch.setattr("codex_claude_orchestrator.cli.build_tmux_console", lambda: fake_console)

    stdout = StringIO()
    with redirect_stdout(stdout):
        list_exit = main(["term", "list"])
    list_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        attach_exit = main(["term", "attach", "--name", "orchestrator-test"])
    attach_payload = json.loads(stdout.getvalue())

    assert list_exit == 0
    assert list_payload == {"sessions": ["orchestrator-test"]}
    assert attach_exit == 0
    assert attach_payload == {"attached": "orchestrator-test", "returncode": 0}
    assert fake_console.attach_calls == ["orchestrator-test"]


def test_term_run_session_uses_tmux_runners(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_engine = FakeSessionEngine()
    build_calls = []
    runner_targets = []

    class FakeTmuxCommandRunner:
        def __init__(self, **kwargs):
            runner_targets.append(kwargs["target_pane"])

        def __call__(self, *args, **kwargs):
            return CompletedProcess(args[0], 0, stdout="", stderr="")

    def fake_build_session_engine(repo_root, worker_runner=None, verification_command_runner=None):
        build_calls.append(
            {
                "repo_root": repo_root,
                "worker_runner": worker_runner,
                "verification_command_runner": verification_command_runner,
            }
        )
        return fake_engine

    monkeypatch.setattr("codex_claude_orchestrator.cli.TmuxCommandRunner", FakeTmuxCommandRunner)
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_session_engine", fake_build_session_engine)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "term",
                "run-session",
                "--tmux-name",
                "orchestrator-test",
                "--goal",
                "Implement via tmux",
                "--repo",
                str(repo_root),
                "--workspace-mode",
                "readonly",
                "--max-rounds",
                "2",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["session_id"] == "session-cli"
    assert runner_targets == ["orchestrator-test:claude.0", "orchestrator-test:verify.0"]
    assert build_calls[0]["repo_root"] == repo_root.resolve()
    assert fake_engine.calls[0]["goal"] == "Implement via tmux"
    assert fake_engine.calls[0]["max_rounds"] == 2


class FakeTmuxConsole:
    def __init__(self):
        self.launch_calls = []
        self.attach_calls = []

    def launch_session_start(self, **kwargs):
        self.launch_calls.append(kwargs)
        return {
            "tmux_session": kwargs["name"],
            "attach_command": f"tmux attach -t {kwargs['name']}",
        }

    def list_sessions(self):
        return ["orchestrator-test"]

    def attach(self, name):
        self.attach_calls.append(name)
        return CompletedProcess(["tmux", "attach", "-t", name], 0)


class FakeClaudeWindowLauncher:
    def __init__(self):
        self.calls = []

    def open(self, **kwargs):
        self.calls.append(kwargs)
        return FakeClaudeWindowLaunch()


class FakeClaudeWindowLaunch:
    def to_dict(self):
        return {
            "run_id": "claude-open-test",
            "repo": "/tmp/repo",
            "prompt_path": "/tmp/repo/.orchestrator/claude-open/claude-open-test/prompt.txt",
            "script_path": "/tmp/repo/.orchestrator/claude-open/claude-open-test/open.zsh",
            "transcript_path": "/tmp/repo/.orchestrator/claude-open/claude-open-test/transcript.txt",
            "terminal_app": "terminal",
            "launched": False,
            "open_command": ["osascript", "-e", "..."],
        }


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
