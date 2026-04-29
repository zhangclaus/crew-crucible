import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.agent_registry import AgentRegistry
from codex_claude_orchestrator.bridge_supervisor_loop import BridgeSupervisorLoop
from codex_claude_orchestrator.claude_bridge import ClaudeBridge
from codex_claude_orchestrator.claude_window import ClaudeWindowLauncher
from codex_claude_orchestrator.models import TaskRecord, WorkspaceMode
from codex_claude_orchestrator.policy_gate import PolicyGate
from codex_claude_orchestrator.prompt_compiler import PromptCompiler
from codex_claude_orchestrator.result_evaluator import ResultEvaluator
from codex_claude_orchestrator.run_recorder import RunRecorder
from codex_claude_orchestrator.session_engine import SessionEngine
from codex_claude_orchestrator.session_recorder import SessionRecorder
from codex_claude_orchestrator.skill_evolution import SkillEvolution
from codex_claude_orchestrator.supervisor import Supervisor
from codex_claude_orchestrator.tmux_console import TmuxCommandRunner, TmuxConsole, build_default_term_name
from codex_claude_orchestrator.ui_server import run_ui_server
from codex_claude_orchestrator.verification_runner import VerificationRunner
from codex_claude_orchestrator.workspace_manager import WorkspaceManager


def add_session_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--goal", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument(
        "--workspace-mode",
        choices=("isolated", "shared", "readonly"),
        default="isolated",
    )
    parser.add_argument("--assigned-agent", default="claude")
    parser.add_argument("--max-rounds", type=int, default=1)
    parser.add_argument("--verification-command", action="append", default=[])
    parser.add_argument(
        "--allow-shared-write",
        action="store_true",
        help="Allow a worker to write directly in shared workspace mode",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dispatch = subparsers.add_parser("dispatch", help="Dispatch a task to a worker")
    dispatch.add_argument("--task-id", required=False)
    dispatch.add_argument("--goal", required=True)
    dispatch.add_argument("--repo", required=True)
    dispatch.add_argument(
        "--workspace-mode",
        choices=("isolated", "shared", "readonly"),
        default="isolated",
    )
    dispatch.add_argument(
        "--allow-shared-write",
        action="store_true",
        help="Allow a worker to write directly in shared workspace mode",
    )
    dispatch.add_argument("--assigned-agent", default="claude")

    agents = subparsers.add_parser("agents", help="Manage configured worker agents")
    agent_subparsers = agents.add_subparsers(dest="agent_command", required=True)
    agent_subparsers.add_parser("list", help="List configured worker agents")

    runs = subparsers.add_parser("runs", help="Inspect recorded orchestrator runs")
    run_subparsers = runs.add_subparsers(dest="run_command", required=True)
    runs_list = run_subparsers.add_parser("list", help="List recorded runs")
    runs_list.add_argument("--repo", required=True)
    runs_show = run_subparsers.add_parser("show", help="Show a recorded run")
    runs_show.add_argument("--repo", required=True)
    runs_show.add_argument("--run-id", required=True)

    session = subparsers.add_parser("session", help="Run adversarial V2 sessions")
    session_subparsers = session.add_subparsers(dest="session_command", required=True)
    session_start = session_subparsers.add_parser("start", help="Start an adversarial session")
    add_session_arguments(session_start)

    sessions = subparsers.add_parser("sessions", help="Inspect adversarial V2 sessions")
    sessions_subparsers = sessions.add_subparsers(dest="sessions_command", required=True)
    sessions_list = sessions_subparsers.add_parser("list", help="List recorded sessions")
    sessions_list.add_argument("--repo", required=True)
    sessions_show = sessions_subparsers.add_parser("show", help="Show a recorded session")
    sessions_show.add_argument("--repo", required=True)
    sessions_show.add_argument("--session-id", required=True)

    skills = subparsers.add_parser("skills", help="Manage evolved local skills")
    skills_subparsers = skills.add_subparsers(dest="skills_command", required=True)
    skills_list = skills_subparsers.add_parser("list", help="List evolved skills")
    skills_list.add_argument("--repo", required=True)
    skills_list.add_argument(
        "--status",
        choices=("pending", "active", "rejected", "archived"),
        required=False,
    )
    skills_show = skills_subparsers.add_parser("show", help="Show an evolved skill")
    skills_show.add_argument("--repo", required=True)
    skills_show.add_argument("--skill-id", required=True)
    skills_approve = skills_subparsers.add_parser("approve", help="Approve a pending skill")
    skills_approve.add_argument("--repo", required=True)
    skills_approve.add_argument("--skill-id", required=True)
    skills_reject = skills_subparsers.add_parser("reject", help="Reject a pending skill")
    skills_reject.add_argument("--repo", required=True)
    skills_reject.add_argument("--skill-id", required=True)
    skills_reject.add_argument("--reason", default="")

    ui = subparsers.add_parser("ui", help="Start the local visual console")
    ui.add_argument("--repo", required=True)
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)

    claude = subparsers.add_parser("claude", help="Open direct Claude CLI windows")
    claude_subparsers = claude.add_subparsers(dest="claude_command", required=True)
    claude_open = claude_subparsers.add_parser("open", help="Open Claude CLI in a Terminal window")
    claude_open.add_argument("--repo", required=True)
    claude_open.add_argument("--goal", required=True)
    claude_open.add_argument(
        "--workspace-mode",
        choices=("readonly", "shared", "isolated"),
        default="readonly",
    )
    claude_open.add_argument("--terminal-app", default="terminal")
    claude_open.add_argument(
        "--dry-run",
        action="store_true",
        help="Create prompt artifacts without opening Terminal",
    )
    claude_bridge = claude_subparsers.add_parser("bridge", help="Keep a resumable Claude CLI conversation")
    claude_bridge_subparsers = claude_bridge.add_subparsers(dest="claude_bridge_command", required=True)
    claude_bridge_start = claude_bridge_subparsers.add_parser("start", help="Start a resumable Claude bridge")
    claude_bridge_start.add_argument("--repo", required=True)
    claude_bridge_start.add_argument("--goal", required=True)
    claude_bridge_start.add_argument(
        "--workspace-mode",
        choices=("readonly", "shared"),
        default="readonly",
    )
    claude_bridge_start.add_argument(
        "--visual",
        choices=("none", "log", "terminal"),
        default="none",
        help="Open an append-only bridge log watcher",
    )
    claude_bridge_start.add_argument("--dry-run", action="store_true")
    claude_bridge_start.add_argument("--supervised", action="store_true")
    claude_bridge_send = claude_bridge_subparsers.add_parser("send", help="Send a follow-up to a Claude bridge")
    claude_bridge_send.add_argument("--repo", required=True)
    claude_bridge_send.add_argument("--bridge-id", required=False)
    claude_bridge_send.add_argument("--message", required=True)
    claude_bridge_send.add_argument("--dry-run", action="store_true")
    claude_bridge_tail = claude_bridge_subparsers.add_parser("tail", help="Show recent bridge turns")
    claude_bridge_tail.add_argument("--repo", required=True)
    claude_bridge_tail.add_argument("--bridge-id", required=False)
    claude_bridge_tail.add_argument("--limit", type=int, default=5)
    claude_bridge_list = claude_bridge_subparsers.add_parser("list", help="List Claude bridges")
    claude_bridge_list.add_argument("--repo", required=True)
    claude_bridge_status = claude_bridge_subparsers.add_parser("status", help="Show supervised bridge status")
    claude_bridge_status.add_argument("--repo", required=True)
    claude_bridge_status.add_argument("--bridge-id", required=False)
    claude_bridge_verify = claude_bridge_subparsers.add_parser("verify", help="Run supervised bridge verification")
    claude_bridge_verify.add_argument("--repo", required=True)
    claude_bridge_verify.add_argument("--bridge-id", required=False)
    claude_bridge_verify.add_argument("--turn-id", required=False)
    claude_bridge_verify.add_argument("--command", required=True)
    claude_bridge_challenge = claude_bridge_subparsers.add_parser("challenge", help="Record a Codex bridge challenge")
    claude_bridge_challenge.add_argument("--repo", required=True)
    claude_bridge_challenge.add_argument("--bridge-id", required=False)
    claude_bridge_challenge.add_argument("--summary", required=True)
    claude_bridge_challenge.add_argument("--repair-goal", required=True)
    claude_bridge_challenge.add_argument("--send", action="store_true")
    claude_bridge_accept = claude_bridge_subparsers.add_parser("accept", help="Accept a supervised bridge")
    claude_bridge_accept.add_argument("--repo", required=True)
    claude_bridge_accept.add_argument("--bridge-id", required=False)
    claude_bridge_accept.add_argument("--summary", required=True)
    claude_bridge_needs_human = claude_bridge_subparsers.add_parser(
        "needs-human",
        help="Mark a supervised bridge as needing human review",
    )
    claude_bridge_needs_human.add_argument("--repo", required=True)
    claude_bridge_needs_human.add_argument("--bridge-id", required=False)
    claude_bridge_needs_human.add_argument("--summary", required=True)
    claude_bridge_supervise = claude_bridge_subparsers.add_parser(
        "supervise",
        help="Run the Codex bridge supervisor loop for an existing supervised bridge",
    )
    claude_bridge_supervise.add_argument("--repo", required=True)
    claude_bridge_supervise.add_argument("--bridge-id", required=False)
    claude_bridge_supervise.add_argument("--verification-command", action="append", default=[])
    claude_bridge_supervise.add_argument("--max-rounds", type=int, default=3)
    claude_bridge_supervise.add_argument("--poll-interval", type=float, default=5.0)
    claude_bridge_run = claude_bridge_subparsers.add_parser(
        "run",
        help="Start a supervised Claude bridge and run the Codex supervisor loop",
    )
    claude_bridge_run.add_argument("--repo", required=True)
    claude_bridge_run.add_argument("--goal", required=True)
    claude_bridge_run.add_argument(
        "--workspace-mode",
        choices=("readonly", "shared"),
        default="readonly",
    )
    claude_bridge_run.add_argument(
        "--visual",
        choices=("none", "log", "terminal"),
        default="none",
    )
    claude_bridge_run.add_argument("--verification-command", action="append", default=[])
    claude_bridge_run.add_argument("--max-rounds", type=int, default=3)
    claude_bridge_run.add_argument("--poll-interval", type=float, default=5.0)

    term = subparsers.add_parser("term", help="Manage tmux terminal consoles")
    term_subparsers = term.add_subparsers(dest="term_command", required=True)
    term_session = term_subparsers.add_parser("session", help="Run sessions in a tmux console")
    term_session_subparsers = term_session.add_subparsers(dest="term_session_command", required=True)
    term_session_start = term_session_subparsers.add_parser("start", help="Start a tmux-backed session")
    term_session_start.add_argument("--name", required=False)
    add_session_arguments(term_session_start)

    term_attach = term_subparsers.add_parser("attach", help="Attach to a tmux console")
    term_attach.add_argument("--name", required=True)
    term_subparsers.add_parser("list", help="List tmux consoles")

    term_run_session = term_subparsers.add_parser("run-session", help="Internal runner used by tmux control windows")
    term_run_session.add_argument("--tmux-name", required=True)
    add_session_arguments(term_run_session)

    subparsers.add_parser("doctor", help="Check local orchestrator prerequisites")
    return parser


def build_supervisor(state_root: Path, worker_runner=None) -> Supervisor:
    return Supervisor(
        prompt_compiler=PromptCompiler(),
        workspace_manager=WorkspaceManager(state_root),
        adapter=ClaudeCliAdapter(runner=worker_runner),
        policy_gate=PolicyGate(),
        run_recorder=RunRecorder(state_root),
        result_evaluator=ResultEvaluator(),
    )


def build_session_engine(repo_root: Path, worker_runner=None, verification_command_runner=None) -> SessionEngine:
    state_root = repo_root / ".orchestrator"
    session_recorder = SessionRecorder(state_root)
    return SessionEngine(
        supervisor=build_supervisor(state_root, worker_runner=worker_runner),
        run_recorder=RunRecorder(state_root),
        session_recorder=session_recorder,
        verification_runner=VerificationRunner(
            repo_root=repo_root,
            session_recorder=session_recorder,
            policy_gate=PolicyGate(),
            runner=verification_command_runner,
        ),
        skill_evolution=SkillEvolution(state_root),
    )


def build_tmux_console() -> TmuxConsole:
    return TmuxConsole()


def build_claude_window_launcher() -> ClaudeWindowLauncher:
    return ClaudeWindowLauncher()


def build_claude_bridge(repo_root: Path) -> ClaudeBridge:
    state_root = repo_root / ".orchestrator"
    session_recorder = SessionRecorder(state_root)
    return ClaudeBridge(
        state_root,
        session_recorder=session_recorder,
        verification_runner=VerificationRunner(
            repo_root=repo_root,
            session_recorder=session_recorder,
            policy_gate=PolicyGate(),
        ),
        result_evaluator=ResultEvaluator(),
    )


def build_bridge_supervisor_loop(bridge: ClaudeBridge) -> BridgeSupervisorLoop:
    return BridgeSupervisorLoop(bridge)


def run_doctor(registry: AgentRegistry) -> dict[str, object]:
    python_ok = sys.version_info >= (3, 11)
    claude_path = shutil.which("claude")
    return {
        "python": {
            "ok": python_ok,
            "version": sys.version.split()[0],
            "required": ">=3.11",
        },
        "claude_cli": {
            "ok": claude_path is not None,
            "path": claude_path,
        },
        "agents": [profile.to_dict() for profile in registry.list_profiles()],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root_command = resolve_root_command(args)
    registry = AgentRegistry.default()

    if root_command == "agents":
        if args.agent_command == "list":
            print(json.dumps({"agents": [profile.to_dict() for profile in registry.list_profiles()]}, ensure_ascii=False))
            return 0
        raise ValueError(f"Unsupported agents command: {args.agent_command}")

    if root_command == "doctor":
        print(json.dumps(run_doctor(registry), ensure_ascii=False))
        return 0

    if root_command == "claude":
        if args.claude_command == "open":
            launch = build_claude_window_launcher().open(
                repo_root=Path(args.repo).resolve(),
                goal=args.goal,
                workspace_mode=args.workspace_mode,
                terminal_app=args.terminal_app,
                dry_run=args.dry_run,
            )
            print(json.dumps(launch.to_dict(), ensure_ascii=False))
            return 0
        if args.claude_command == "bridge":
            repo_root = Path(args.repo).resolve()
            bridge = build_claude_bridge(repo_root)
            if args.claude_bridge_command == "start":
                print(
                    json.dumps(
                        bridge.start(
                            repo_root=repo_root,
                            goal=args.goal,
                            workspace_mode=args.workspace_mode,
                            visual=args.visual,
                            dry_run=args.dry_run,
                            supervised=args.supervised,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "send":
                print(
                    json.dumps(
                        bridge.send(
                            repo_root=repo_root,
                            bridge_id=args.bridge_id,
                            message=args.message,
                            dry_run=args.dry_run,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "tail":
                print(
                    json.dumps(
                        bridge.tail(
                            repo_root=repo_root,
                            bridge_id=args.bridge_id,
                            limit=args.limit,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "list":
                print(json.dumps({"bridges": bridge.list(repo_root=repo_root)}, ensure_ascii=False))
                return 0
            if args.claude_bridge_command == "status":
                print(
                    json.dumps(
                        bridge.status(repo_root=repo_root, bridge_id=args.bridge_id),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "verify":
                print(
                    json.dumps(
                        bridge.verify(
                            repo_root=repo_root,
                            bridge_id=args.bridge_id,
                            command=args.command,
                            turn_id=args.turn_id,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "challenge":
                print(
                    json.dumps(
                        bridge.challenge(
                            repo_root=repo_root,
                            bridge_id=args.bridge_id,
                            summary=args.summary,
                            repair_goal=args.repair_goal,
                            send=args.send,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "accept":
                print(
                    json.dumps(
                        bridge.accept(repo_root=repo_root, bridge_id=args.bridge_id, summary=args.summary),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "needs-human":
                print(
                    json.dumps(
                        bridge.needs_human(repo_root=repo_root, bridge_id=args.bridge_id, summary=args.summary),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "supervise":
                loop = build_bridge_supervisor_loop(bridge)
                print(
                    json.dumps(
                        loop.supervise(
                            repo_root=repo_root,
                            bridge_id=args.bridge_id,
                            verification_commands=args.verification_command,
                            max_rounds=args.max_rounds,
                            poll_interval_seconds=args.poll_interval,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
            if args.claude_bridge_command == "run":
                loop = build_bridge_supervisor_loop(bridge)
                print(
                    json.dumps(
                        loop.run(
                            repo_root=repo_root,
                            goal=args.goal,
                            workspace_mode=args.workspace_mode,
                            visual=args.visual,
                            verification_commands=args.verification_command,
                            max_rounds=args.max_rounds,
                            poll_interval_seconds=args.poll_interval,
                        ),
                        ensure_ascii=False,
                    )
                )
                return 0
            raise ValueError(f"Unsupported claude bridge command: {args.claude_bridge_command}")
        raise ValueError(f"Unsupported claude command: {args.claude_command}")

    if root_command == "term":
        return handle_term_command(args, registry)

    if root_command == "runs":
        recorder = RunRecorder(Path(args.repo).resolve() / ".orchestrator")
        if args.run_command == "list":
            print(json.dumps({"runs": recorder.list_runs()}, ensure_ascii=False))
            return 0
        if args.run_command == "show":
            print(json.dumps(recorder.read_run(args.run_id), ensure_ascii=False))
            return 0
        raise ValueError(f"Unsupported runs command: {args.run_command}")

    if root_command == "session":
        if args.session_command == "start":
            repo_root = Path(args.repo).resolve()
            workspace_mode = WorkspaceMode(args.workspace_mode)
            profile = registry.get(args.assigned_agent)
            engine = build_session_engine(repo_root)
            session = engine.start(
                repo_root=repo_root,
                goal=args.goal,
                assigned_agent=profile.name,
                workspace_mode=workspace_mode,
                allowed_tools=registry.allowed_tools(
                    profile.name,
                    workspace_mode,
                    shared_write_allowed=args.allow_shared_write,
                ),
                max_rounds=args.max_rounds,
                verification_commands=args.verification_command,
                shared_write_allowed=args.allow_shared_write,
            )
            print(json.dumps(session.to_dict(), ensure_ascii=False))
            return 0
        raise ValueError(f"Unsupported session command: {args.session_command}")

    if root_command == "sessions":
        recorder = SessionRecorder(Path(args.repo).resolve() / ".orchestrator")
        if args.sessions_command == "list":
            print(json.dumps({"sessions": recorder.list_sessions()}, ensure_ascii=False))
            return 0
        if args.sessions_command == "show":
            print(json.dumps(recorder.read_session(args.session_id), ensure_ascii=False))
            return 0
        raise ValueError(f"Unsupported sessions command: {args.sessions_command}")

    if root_command == "skills":
        from codex_claude_orchestrator.models import SkillStatus

        evolution = SkillEvolution(Path(args.repo).resolve() / ".orchestrator")
        if args.skills_command == "list":
            status = SkillStatus(args.status) if args.status else None
            print(json.dumps({"skills": evolution.list_skills(status)}, ensure_ascii=False))
            return 0
        if args.skills_command == "show":
            print(json.dumps(evolution.show_skill(args.skill_id), ensure_ascii=False))
            return 0
        if args.skills_command == "approve":
            print(json.dumps(evolution.approve_skill(args.skill_id).to_dict(), ensure_ascii=False))
            return 0
        if args.skills_command == "reject":
            print(
                json.dumps(
                    evolution.reject_skill(args.skill_id, reason=args.reason).to_dict(),
                    ensure_ascii=False,
                )
            )
            return 0
        raise ValueError(f"Unsupported skills command: {args.skills_command}")

    if root_command == "ui":
        result = run_ui_server(
            repo_root=Path(args.repo).resolve(),
            host=args.host,
            port=args.port,
        )
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 0

    if root_command != "dispatch":
        raise ValueError(f"Unsupported command: {root_command}")

    repo_root = Path(args.repo).resolve()
    workspace_mode = WorkspaceMode(args.workspace_mode)
    profile = registry.get(args.assigned_agent)
    supervisor = build_supervisor(repo_root / ".orchestrator")
    task = TaskRecord(
        task_id=args.task_id or f"task-{uuid4()}",
        parent_task_id=None,
        origin="cli",
        assigned_agent=profile.name,
        goal=args.goal,
        task_type="adhoc",
        scope=str(repo_root),
        workspace_mode=workspace_mode,
        allowed_tools=registry.allowed_tools(
            profile.name,
            workspace_mode,
            shared_write_allowed=args.allow_shared_write,
        ),
        shared_write_allowed=args.allow_shared_write,
    )
    outcome = supervisor.dispatch(task, repo_root)
    print(json.dumps(outcome.to_dict(), ensure_ascii=False))
    return 0


def resolve_root_command(args: argparse.Namespace) -> str:
    for attr, command in (
        ("agent_command", "agents"),
        ("run_command", "runs"),
        ("session_command", "session"),
        ("sessions_command", "sessions"),
        ("skills_command", "skills"),
        ("claude_command", "claude"),
        ("term_command", "term"),
    ):
        if getattr(args, attr, None) is not None:
            return command
    return args.command


def handle_term_command(args, registry: AgentRegistry) -> int:
    console = build_tmux_console()
    if args.term_command == "list":
        print(json.dumps({"sessions": console.list_sessions()}, ensure_ascii=False))
        return 0
    if args.term_command == "attach":
        result = console.attach(args.name)
        print(json.dumps({"attached": args.name, "returncode": result.returncode}, ensure_ascii=False))
        return result.returncode
    if args.term_command == "session":
        if args.term_session_command != "start":
            raise ValueError(f"Unsupported term session command: {args.term_session_command}")
        repo_root = Path(args.repo).resolve()
        name = args.name or build_default_term_name(repo_root)
        payload = console.launch_session_start(
            name=name,
            repo_root=repo_root,
            orchestrator_executable=str(Path(sys.argv[0]).resolve()),
            session_args=build_session_cli_args(args, repo_root),
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    if args.term_command == "run-session":
        session = run_tmux_backed_session(args, registry)
        print(json.dumps(session.to_dict(), ensure_ascii=False))
        return 0
    raise ValueError(f"Unsupported term command: {args.term_command}")


def build_session_cli_args(args, repo_root: Path) -> list[str]:
    command = [
        "--goal",
        args.goal,
        "--repo",
        str(repo_root),
        "--workspace-mode",
        args.workspace_mode,
        "--assigned-agent",
        args.assigned_agent,
        "--max-rounds",
        str(args.max_rounds),
    ]
    for verification_command in args.verification_command:
        command.extend(["--verification-command", verification_command])
    if args.allow_shared_write:
        command.append("--allow-shared-write")
    return command


def run_tmux_backed_session(args, registry: AgentRegistry):
    repo_root = Path(args.repo).resolve()
    state_root = repo_root / ".orchestrator"
    worker_runner = TmuxCommandRunner(
        target_pane=f"{args.tmux_name}:claude.0",
        log_root=state_root / "term" / args.tmux_name / "claude",
    )
    verification_command_runner = TmuxCommandRunner(
        target_pane=f"{args.tmux_name}:verify.0",
        log_root=state_root / "term" / args.tmux_name / "verify",
    )
    engine = build_session_engine(
        repo_root,
        worker_runner=worker_runner,
        verification_command_runner=verification_command_runner,
    )
    workspace_mode = WorkspaceMode(args.workspace_mode)
    profile = registry.get(args.assigned_agent)
    return engine.start(
        repo_root=repo_root,
        goal=args.goal,
        assigned_agent=profile.name,
        workspace_mode=workspace_mode,
        allowed_tools=registry.allowed_tools(
            profile.name,
            workspace_mode,
            shared_write_allowed=args.allow_shared_write,
        ),
        max_rounds=args.max_rounds,
        verification_commands=args.verification_command,
        shared_write_allowed=args.allow_shared_write,
    )
