from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from codex_claude_orchestrator.cli import main
from codex_claude_orchestrator.core.models import (
    EvaluationOutcome,
    LearningNote,
    NextAction,
    RunRecord,
    SessionRecord,
    SessionStatus,
    TaskRecord,
    WorkspaceMode,
)
from codex_claude_orchestrator.crew.models import WorkerRole
from codex_claude_orchestrator.state.run_recorder import RunRecorder
from codex_claude_orchestrator.state.session_recorder import SessionRecorder
from codex_claude_orchestrator.session.skill_evolution import SkillEvolution


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


def test_claude_bridge_commands_route_to_bridge(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_bridge = FakeClaudeBridge()

    monkeypatch.setattr("codex_claude_orchestrator.cli.build_claude_bridge", lambda repo_root: fake_bridge)

    stdout = StringIO()
    with redirect_stdout(stdout):
        start_exit = main(
            [
                "claude",
                "bridge",
                "start",
                "--repo",
                str(repo_root),
                "--goal",
                "Inspect repo",
                "--workspace-mode",
                "readonly",
                "--visual",
                "log",
                "--supervised",
            ]
        )
    start_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        send_exit = main(
            [
                "claude",
                "bridge",
                "send",
                "--repo",
                str(repo_root),
                "--message",
                "继续",
            ]
        )
    send_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        tail_exit = main(["claude", "bridge", "tail", "--repo", str(repo_root), "--limit", "1"])
    tail_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        list_exit = main(["claude", "bridge", "list", "--repo", str(repo_root)])
    list_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        status_exit = main(["claude", "bridge", "status", "--repo", str(repo_root)])
    status_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        verify_exit = main(
            [
                "claude",
                "bridge",
                "verify",
                "--repo",
                str(repo_root),
                "--command",
                "pytest -q",
            ]
        )
    verify_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        challenge_exit = main(
            [
                "claude",
                "bridge",
                "challenge",
                "--repo",
                str(repo_root),
                "--summary",
                "missing verification",
                "--repair-goal",
                "run pytest",
                "--send",
            ]
        )
    challenge_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        accept_exit = main(
            [
                "claude",
                "bridge",
                "accept",
                "--repo",
                str(repo_root),
                "--summary",
                "accepted",
            ]
        )
    accept_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        needs_human_exit = main(
            [
                "claude",
                "bridge",
                "needs-human",
                "--repo",
                str(repo_root),
                "--summary",
                "need input",
            ]
        )
    needs_human_payload = json.loads(stdout.getvalue())

    assert start_exit == 0
    assert send_exit == 0
    assert tail_exit == 0
    assert list_exit == 0
    assert status_exit == 0
    assert verify_exit == 0
    assert challenge_exit == 0
    assert accept_exit == 0
    assert needs_human_exit == 0
    assert start_payload["bridge"]["bridge_id"] == "bridge-cli"
    assert send_payload["latest_turn"]["message"] == "继续"
    assert tail_payload["turns"][0]["turn_id"] == "turn-cli"
    assert list_payload["bridges"][0]["bridge_id"] == "bridge-cli"
    assert status_payload["status"] == "supervised"
    assert verify_payload["verification"]["command"] == "pytest -q"
    assert challenge_payload["challenge"]["repair_goal"] == "run pytest"
    assert accept_payload["accepted"] is True
    assert needs_human_payload["needs_human"] is True
    assert fake_bridge.calls == [
        {
            "method": "start",
            "repo_root": repo_root.resolve(),
            "goal": "Inspect repo",
            "workspace_mode": "readonly",
            "visual": "log",
            "dry_run": False,
            "supervised": True,
        },
        {
            "method": "send",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "message": "继续",
            "dry_run": False,
        },
        {
            "method": "tail",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "limit": 1,
        },
        {"method": "list", "repo_root": repo_root.resolve()},
        {"method": "status", "repo_root": repo_root.resolve(), "bridge_id": None},
        {
            "method": "verify",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "command": "pytest -q",
            "turn_id": None,
        },
        {
            "method": "challenge",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "summary": "missing verification",
            "repair_goal": "run pytest",
            "send": True,
        },
        {
            "method": "accept",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "summary": "accepted",
        },
        {
            "method": "needs_human",
            "repo_root": repo_root.resolve(),
            "bridge_id": None,
            "summary": "need input",
        },
    ]


def test_claude_bridge_supervisor_commands_route_to_loop(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_bridge = FakeClaudeBridge()
    fake_loop = FakeBridgeSupervisorLoop()

    monkeypatch.setattr("codex_claude_orchestrator.cli.build_claude_bridge", lambda repo_root: fake_bridge)
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_bridge_supervisor_loop", lambda bridge: fake_loop)

    stdout = StringIO()
    with redirect_stdout(stdout):
        supervise_exit = main(
            [
                "claude",
                "bridge",
                "supervise",
                "--repo",
                str(repo_root),
                "--bridge-id",
                "bridge-existing",
                "--verification-command",
                "pytest -q",
                "--max-rounds",
                "2",
                "--poll-interval",
                "0",
            ]
        )
    supervise_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        run_exit = main(
            [
                "claude",
                "bridge",
                "run",
                "--repo",
                str(repo_root),
                "--goal",
                "Implement feature",
                "--workspace-mode",
                "shared",
                "--visual",
                "log",
                "--verification-command",
                "pytest -q",
                "--verification-command",
                "ruff check .",
                "--max-rounds",
                "3",
                "--poll-interval",
                "0",
            ]
        )
    run_payload = json.loads(stdout.getvalue())

    assert supervise_exit == 0
    assert run_exit == 0
    assert supervise_payload["mode"] == "supervise"
    assert run_payload["mode"] == "run"
    assert fake_loop.calls == [
        {
            "method": "supervise",
            "repo_root": repo_root.resolve(),
            "bridge_id": "bridge-existing",
            "verification_commands": ["pytest -q"],
            "max_rounds": 2,
            "poll_interval_seconds": 0.0,
        },
        {
            "method": "run",
            "repo_root": repo_root.resolve(),
            "goal": "Implement feature",
            "workspace_mode": "shared",
            "visual": "log",
            "verification_commands": ["pytest -q", "ruff check ."],
            "max_rounds": 3,
            "poll_interval_seconds": 0.0,
        },
    ]


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


class FakeClaudeBridge:
    def __init__(self):
        self.calls = []

    def start(self, **kwargs):
        self.calls.append({"method": "start", **kwargs})
        return {"bridge": {"bridge_id": "bridge-cli"}, "latest_turn": {"turn_id": "turn-cli"}}

    def send(self, **kwargs):
        self.calls.append({"method": "send", **kwargs})
        return {
            "bridge": {"bridge_id": "bridge-cli"},
            "latest_turn": {"turn_id": "turn-cli", "message": kwargs["message"]},
        }

    def tail(self, **kwargs):
        self.calls.append({"method": "tail", **kwargs})
        return {"bridge": {"bridge_id": "bridge-cli"}, "turns": [{"turn_id": "turn-cli"}]}

    def list(self, **kwargs):
        self.calls.append({"method": "list", **kwargs})
        return [{"bridge_id": "bridge-cli"}]

    def status(self, **kwargs):
        self.calls.append({"method": "status", **kwargs})
        return {"status": "supervised", "bridge_id": kwargs["bridge_id"]}

    def verify(self, **kwargs):
        self.calls.append({"method": "verify", **kwargs})
        return {"verification": {"command": kwargs["command"], "turn_id": kwargs["turn_id"]}}

    def challenge(self, **kwargs):
        self.calls.append({"method": "challenge", **kwargs})
        return {"challenge": {"summary": kwargs["summary"], "repair_goal": kwargs["repair_goal"]}}

    def accept(self, **kwargs):
        self.calls.append({"method": "accept", **kwargs})
        return {"accepted": True, "summary": kwargs["summary"]}

    def needs_human(self, **kwargs):
        self.calls.append({"method": "needs_human", **kwargs})
        return {"needs_human": True, "summary": kwargs["summary"]}


class FakeBridgeSupervisorLoop:
    def __init__(self):
        self.calls = []

    def supervise(self, **kwargs):
        self.calls.append({"method": "supervise", **kwargs})
        return {"mode": "supervise", "status": "accepted"}

    def run(self, **kwargs):
        self.calls.append({"method": "run", **kwargs})
        return {"mode": "run", "status": "accepted"}


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


class FakeCrewController:
    def __init__(self):
        self.calls = []

    def start(self, **kwargs):
        self.calls.append({"method": "start", **kwargs})
        return type(
            "Crew",
            (),
            {
                "crew_id": "crew-cli",
                "status": "running",
                "to_dict": lambda self: {"crew_id": "crew-cli", "status": "running"},
            },
        )()

    def start_dynamic(self, **kwargs):
        self.calls.append({"method": "start_dynamic", **kwargs})
        return type(
            "Crew",
            (),
            {
                "crew_id": "crew-cli",
                "status": "running",
                "to_dict": lambda self: {"crew_id": "crew-cli", "status": "running", "active_worker_ids": []},
            },
        )()

    def status(self, **kwargs):
        self.calls.append({"method": "status", **kwargs})
        return {"crew": {"crew_id": kwargs["crew_id"]}}

    def blackboard_entries(self, **kwargs):
        self.calls.append({"method": "blackboard", **kwargs})
        return [{"entry_id": "entry-cli"}]

    def resume_context(self, **kwargs):
        self.calls.append({"method": "resume_context", **kwargs})
        return {"crew": {"crew_id": kwargs["crew_id"]}, "resume_hint": "resume safely"}

    def send_worker(self, **kwargs):
        self.calls.append({"method": "send_worker", **kwargs})
        return {"marker_seen": True}

    def observe_worker(self, **kwargs):
        self.calls.append({"method": "observe_worker", **kwargs})
        return {"snapshot": "Claude is reading"}

    def attach_worker(self, **kwargs):
        self.calls.append({"method": "attach_worker", **kwargs})
        return {"attach_command": "tmux attach -t crew-cli-worker"}

    def tail_worker(self, **kwargs):
        self.calls.append({"method": "tail_worker", **kwargs})
        return {"lines": ["one"]}

    def status_worker(self, **kwargs):
        self.calls.append({"method": "status_worker", **kwargs})
        return {"running": True}

    def stop_worker(self, **kwargs):
        self.calls.append({"method": "stop_worker", **kwargs})
        return {"stopped": True, "worker_id": kwargs["worker_id"]}

    def stop(self, **kwargs):
        self.calls.append({"method": "stop", **kwargs})
        return {"crew_id": kwargs["crew_id"], "stopped_workers": [{"worker_id": "worker-explorer"}]}

    def prune_orphans(self, **kwargs):
        self.calls.append({"method": "prune_orphans", **kwargs})
        return {"active_sessions": ["crew-cli-worker"], "pruned_sessions": ["crew-worker-old"]}


class FakeCrewSupervisorLoop:
    def __init__(self):
        self.calls = []

    def supervise(self, **kwargs):
        self.calls.append({"method": "supervise", **kwargs})
        return {"crew_id": kwargs["crew_id"], "status": "ready_for_codex_accept"}

    def run(self, **kwargs):
        self.calls.append({"method": "run", **kwargs})
        return {"crew_id": "crew-cli", "status": "ready_for_codex_accept"}


class FakeV4MergeTransaction:
    def __init__(self, response=None):
        self.response = response or {"crew_id": "crew-cli", "status": "accepted"}
        self.calls = []

    def accept(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_build_parser_exposes_crew_start_and_worker_commands():
    from codex_claude_orchestrator.cli import build_parser

    parser = build_parser()
    subparsers_action = next(action for action in parser._actions if action.dest == "command")

    assert "crew" in subparsers_action.choices


def test_main_crew_capabilities_list_prints_builtin_dynamic_vocabulary(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["crew", "capabilities", "list", "--repo", str(repo_root)])

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert "inspect_code" in payload["capabilities"]
    assert "review_patch" in payload["capabilities"]


def test_main_crew_start_prints_json_and_propagates_dirty_flag(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_controller = FakeCrewController()
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_controller", lambda repo_root: fake_controller)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "crew",
                "start",
                "--repo",
                str(repo_root),
                "--goal",
                "Build V3 MVP",
                "--workers",
                "explorer,implementer",
                "--allow-dirty-base",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["crew_id"] == "crew-cli"
    assert fake_controller.calls[0]["allow_dirty_base"] is True


def test_main_crew_start_defaults_to_dynamic_control_plane(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_controller = FakeCrewController()
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_controller", lambda repo_root: fake_controller)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "crew",
                "start",
                "--repo",
                str(repo_root),
                "--goal",
                "修复 README typo",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["spawn_policy"] == "dynamic"
    assert payload["active_worker_ids"] == []
    assert fake_controller.calls[0]["method"] == "start_dynamic"


def test_main_crew_worker_observe_routes_to_controller(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_controller = FakeCrewController()
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_controller", lambda repo_root: fake_controller)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "crew",
                "worker",
                "observe",
                "--repo",
                str(repo_root),
                "--crew",
                "crew-cli",
                "--worker",
                "worker-explorer",
                "--lines",
                "120",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["snapshot"] == "Claude is reading"
    assert fake_controller.calls[0]["lines"] == 120


def test_main_crew_stop_and_prune_route_to_controller(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_controller = FakeCrewController()
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_controller", lambda repo_root: fake_controller)

    stdout = StringIO()
    with redirect_stdout(stdout):
        worker_stop_exit = main(
            [
                "crew",
                "worker",
                "stop",
                "--repo",
                str(repo_root),
                "--crew",
                "crew-cli",
                "--worker",
                "worker-explorer",
            ]
        )
    worker_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        crew_stop_exit = main(["crew", "stop", "--repo", str(repo_root), "--crew", "crew-cli"])
    crew_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        prune_exit = main(["crew", "prune", "--repo", str(repo_root)])
    prune_payload = json.loads(stdout.getvalue())

    assert worker_stop_exit == 0
    assert crew_stop_exit == 0
    assert prune_exit == 0
    assert worker_payload["stopped"] is True
    assert crew_payload["stopped_workers"][0]["worker_id"] == "worker-explorer"
    assert prune_payload["pruned_sessions"] == ["crew-worker-old"]
    assert [call["method"] for call in fake_controller.calls] == ["stop_worker", "stop", "prune_orphans"]


def test_main_crew_worker_stop_accepts_workspace_cleanup_policy(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_controller = FakeCrewController()
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_controller", lambda repo_root: fake_controller)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "crew",
                "worker",
                "stop",
                "--repo",
                str(repo_root),
                "--crew",
                "crew-cli",
                "--worker",
                "worker-implementer",
                "--workspace-cleanup",
                "remove",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["stopped"] is True
    assert fake_controller.calls[0]["workspace_cleanup"] == "remove"


def test_main_crew_accept_routes_to_v4_merge_transaction(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_controller = FakeCrewController()
    fake_transaction = FakeV4MergeTransaction(
        {"crew_id": "crew-cli", "status": "accepted", "summary": "accepted"}
    )
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_controller", lambda repo_root: fake_controller)
    monkeypatch.setattr(
        "codex_claude_orchestrator.cli.build_v4_merge_transaction",
        lambda repo_root, recorder, controller: fake_transaction,
    )

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "crew",
                "accept",
                "--repo",
                str(repo_root),
                "--crew",
                "crew-cli",
                "--summary",
                "accepted",
                "--verification-command",
                "pytest -q",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["status"] == "accepted"
    assert fake_transaction.calls == [
        {
            "crew_id": "crew-cli",
            "summary": "accepted",
            "verification_commands": ["pytest -q"],
        }
    ]
    assert fake_controller.calls == []


def test_main_crew_accept_without_verification_command_is_blocked(
    tmp_path: Path,
    monkeypatch,
):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_controller = FakeCrewController()
    fake_transaction = FakeV4MergeTransaction(
        {
            "crew_id": "crew-cli",
            "status": "blocked",
            "reason": "verification command required",
        }
    )
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_controller", lambda repo_root: fake_controller)
    monkeypatch.setattr(
        "codex_claude_orchestrator.cli.build_v4_merge_transaction",
        lambda repo_root, recorder, controller: fake_transaction,
    )

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "crew",
                "accept",
                "--repo",
                str(repo_root),
                "--crew",
                "crew-cli",
                "--summary",
                "accepted",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["status"] == "blocked"
    assert payload["reason"] == "verification command required"
    assert fake_transaction.calls[0]["verification_commands"] == []
    assert fake_controller.calls == []


def test_main_crew_resume_context_prints_replay_payload(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_controller = FakeCrewController()
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_controller", lambda repo_root: fake_controller)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["crew", "resume-context", "--repo", str(repo_root), "--crew", "crew-cli"])

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["crew"]["crew_id"] == "crew-cli"
    assert payload["resume_hint"] == "resume safely"
    assert fake_controller.calls[0]["method"] == "resume_context"


def test_main_crew_supervise_and_run_route_to_supervisor_loop(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_controller = FakeCrewController()
    fake_loop = FakeCrewSupervisorLoop()
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_controller", lambda repo_root: fake_controller)
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_supervisor_loop", lambda controller: fake_loop)

    stdout = StringIO()
    with redirect_stdout(stdout):
        supervise_exit = main(
            [
                "crew",
                "supervise",
                "--repo",
                str(repo_root),
                "--crew",
                "crew-cli",
                "--verification-command",
                "pytest -q",
                "--max-rounds",
                "2",
                "--poll-interval",
                "0",
            ]
        )
    supervise_payload = json.loads(stdout.getvalue())

    stdout = StringIO()
    with redirect_stdout(stdout):
        run_exit = main(
            [
                "crew",
                "run",
                "--repo",
                str(repo_root),
                "--goal",
                "Build V3 MVP",
                "--verification-command",
                "pytest -q",
                "--max-rounds",
                "3",
                "--poll-interval",
                "0",
                "--allow-dirty-base",
            ]
        )
    run_payload = json.loads(stdout.getvalue())

    assert supervise_exit == 0
    assert run_exit == 0
    assert supervise_payload["status"] == "ready_for_codex_accept"
    assert run_payload["crew_id"] == "crew-cli"
    assert fake_loop.calls == [
        {
            "method": "supervise",
            "repo_root": repo_root.resolve(),
            "crew_id": "crew-cli",
            "verification_commands": ["pytest -q"],
            "max_rounds": 2,
            "poll_interval_seconds": 0.0,
        },
        {
            "method": "run",
            "repo_root": repo_root.resolve(),
            "goal": "Build V3 MVP",
            "verification_commands": ["pytest -q"],
            "max_rounds": 3,
            "poll_interval_seconds": 0.0,
            "allow_dirty_base": True,
            "spawn_policy": "dynamic",
            "seed_contract": None,
        },
    ]


def test_main_crew_run_defaults_to_dynamic_even_for_review_heavy_goal(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_controller = FakeCrewController()
    fake_loop = FakeCrewSupervisorLoop()
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_controller", lambda repo_root: fake_controller)
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_supervisor_loop", lambda controller: fake_loop)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "crew",
                "run",
                "--repo",
                str(repo_root),
                "--goal",
                "让 Claude 检查这个项目，根据 llm-wiki 思想完善代码",
                "--verification-command",
                "pytest -q",
                "--poll-interval",
                "0",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["spawn_policy"] == "dynamic"
    assert fake_loop.calls[0]["spawn_policy"] == "dynamic"
    assert "worker_roles" not in fake_loop.calls[0]


def test_main_crew_run_can_use_static_legacy_worker_selection(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_controller = FakeCrewController()
    fake_loop = FakeCrewSupervisorLoop()
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_controller", lambda repo_root: fake_controller)
    monkeypatch.setattr("codex_claude_orchestrator.cli.build_crew_supervisor_loop", lambda controller: fake_loop)

    stdout = StringIO()
    with redirect_stdout(stdout):
        exit_code = main(
            [
                "crew",
                "run",
                "--repo",
                str(repo_root),
                "--goal",
                "让 Claude 检查这个项目，根据 llm-wiki 思想完善代码",
                "--verification-command",
                "pytest -q",
                "--poll-interval",
                "0",
                "--spawn-policy",
                "static",
            ]
        )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert payload["selected_workers"] == ["explorer", "implementer", "reviewer"]
    assert payload["selection_mode"] == "full"
    assert fake_loop.calls[0]["worker_roles"] == [WorkerRole.EXPLORER, WorkerRole.IMPLEMENTER, WorkerRole.REVIEWER]


def test_cli_crew_events_lists_v4_events(tmp_path, monkeypatch):
    from codex_claude_orchestrator.cli import main
    from codex_claude_orchestrator.v4.event_store import SQLiteEventStore

    monkeypatch.setenv("V4_EVENT_STORE_BACKEND", "sqlite")
    store_path = tmp_path / ".orchestrator" / "v4" / "events.sqlite3"
    store = SQLiteEventStore(store_path)
    store.append(stream_id="crew-1", type="crew.started", crew_id="crew-1", payload={"goal": "Fix tests"})

    stdout = StringIO()
    with redirect_stdout(stdout):
        result = main(["crew", "events", "--repo", str(tmp_path), "--crew", "crew-1"])

    assert result == 0
    assert "crew.started" in stdout.getvalue()


def test_cli_crew_events_uses_v4_event_store_factory(tmp_path, monkeypatch):
    from codex_claude_orchestrator.cli import main
    from codex_claude_orchestrator.v4.events import AgentEvent

    calls = []

    class FakeStore:
        def list_stream(self, stream_id: str, after_sequence: int = 0):
            calls.append({"stream_id": stream_id, "after_sequence": after_sequence})
            return [
                AgentEvent(
                    event_id="evt-1",
                    stream_id=stream_id,
                    sequence=1,
                    type="crew.started",
                    crew_id=stream_id,
                )
            ]

    def fake_factory(repo_root, *, readonly=False):
        calls.append({"repo_root": repo_root, "readonly": readonly})
        return FakeStore()

    monkeypatch.setattr("codex_claude_orchestrator.cli.build_v4_event_store", fake_factory)

    stdout = StringIO()
    with redirect_stdout(stdout):
        result = main(["crew", "events", "--repo", str(tmp_path), "--crew", "crew-1"])

    assert result == 0
    assert "crew.started" in stdout.getvalue()
    assert calls[0] == {"repo_root": tmp_path.resolve(), "readonly": True}
    assert calls[1] == {"stream_id": "crew-1", "after_sequence": 0}


def test_cli_crew_events_without_v4_db_is_read_only(tmp_path, monkeypatch):
    from codex_claude_orchestrator.cli import main

    monkeypatch.setenv("V4_EVENT_STORE_BACKEND", "sqlite")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    stdout = StringIO()
    with redirect_stdout(stdout):
        result = main(["crew", "events", "--repo", str(repo_root), "--crew", "crew-1"])

    assert result == 0
    assert json.loads(stdout.getvalue()) == []
    assert not (repo_root / ".orchestrator").exists()
    assert not (repo_root / ".orchestrator" / "v4" / "events.sqlite3").exists()


def test_cli_crew_events_existing_empty_db_is_read_only(tmp_path, monkeypatch):
    from codex_claude_orchestrator.cli import main

    monkeypatch.setenv("V4_EVENT_STORE_BACKEND", "sqlite")
    repo_root = tmp_path / "repo"
    event_store_path = repo_root / ".orchestrator" / "v4" / "events.sqlite3"
    event_store_path.parent.mkdir(parents=True)
    event_store_path.write_bytes(b"")

    stdout = StringIO()
    with redirect_stdout(stdout):
        result = main(["crew", "events", "--repo", str(repo_root), "--crew", "crew-1"])

    assert result == 0
    assert json.loads(stdout.getvalue()) == []
    assert event_store_path.read_bytes() == b""


def test_cli_crew_events_missing_repo_does_not_create_state(tmp_path):
    from codex_claude_orchestrator.cli import main

    repo_root = tmp_path / "missing"

    with pytest.raises(ValueError, match=f"repo does not exist: {repo_root.resolve()}"):
        main(["crew", "events", "--repo", str(repo_root), "--crew", "crew-1"])

    assert not repo_root.exists()
