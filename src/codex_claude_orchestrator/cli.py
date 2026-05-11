import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.session.agent_registry import AgentRegistry
from codex_claude_orchestrator.state.blackboard import BlackboardStore
from codex_claude_orchestrator.crew.controller import CrewController
from codex_claude_orchestrator.crew.models import WorkerRole
from codex_claude_orchestrator.messaging.message_bus import AgentMessageBus
from codex_claude_orchestrator.messaging.protocol_requests import ProtocolRequestStore
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.verification.crew_runner import CrewVerificationRunner
from codex_claude_orchestrator.crew.merge_arbiter import MergeArbiter
from codex_claude_orchestrator.core.models import TaskRecord, WorkspaceMode
from codex_claude_orchestrator.runtime.native_claude_session import NativeClaudeSession
from codex_claude_orchestrator.core.policy_gate import PolicyGate
from codex_claude_orchestrator.session.prompt_compiler import PromptCompiler
from codex_claude_orchestrator.verification.result_evaluator import ResultEvaluator
from codex_claude_orchestrator.state.run_recorder import RunRecorder
from codex_claude_orchestrator.session.supervisor import Supervisor
from codex_claude_orchestrator.crew.task_graph import TaskGraphPlanner
from codex_claude_orchestrator.v4.adapters.tmux_claude import ClaudeCodeTmuxAdapter
from codex_claude_orchestrator.v4.artifacts import ArtifactStore
from codex_claude_orchestrator.v4.crew_runner import V4CrewRunner
from codex_claude_orchestrator.v4.event_store_factory import build_v4_event_store
from codex_claude_orchestrator.v4.merge_transaction import V4MergeTransaction
from codex_claude_orchestrator.v4.message_ack import MessageAckProcessor
from codex_claude_orchestrator.v4.supervisor import V4Supervisor
from codex_claude_orchestrator.v4.turn_context import TurnContextBuilder
from codex_claude_orchestrator.workers.change_recorder import WorkerChangeRecorder
from codex_claude_orchestrator.workers.pool import WorkerPool
from codex_claude_orchestrator.workers.selection import WorkerSelectionPolicy
from codex_claude_orchestrator.workspace.worktree_manager import WorktreeManager
from codex_claude_orchestrator.workspace.manager import WorkspaceManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="acr", description="Multi-agent adversarial code review for Claude Code")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Generate .mcp.json for Claude Code integration")
    subparsers.add_parser("doctor", help="Check local prerequisites")

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

    crew = subparsers.add_parser("crew", help="Run Codex-managed Claude crews")
    crew_subparsers = crew.add_subparsers(dest="crew_command", required=True)
    crew_start = crew_subparsers.add_parser("start", help="Start a Claude crew")
    crew_start.add_argument("--repo", required=True)
    crew_start.add_argument("--goal", required=True)
    crew_start.add_argument("--workers", default="auto")
    crew_start.add_argument("--mode", choices=("auto", "quick", "standard", "full"), default="auto")
    crew_start.add_argument("--spawn-policy", choices=("dynamic", "static"), default="dynamic")
    crew_start.add_argument("--seed-contract", required=False)
    crew_start.add_argument("--allow-dirty-base", action="store_true")
    crew_run = crew_subparsers.add_parser("run", help="Start a crew and run the supervisor loop")
    crew_run.add_argument("--repo", required=True)
    crew_run.add_argument("--goal", required=True)
    crew_run.add_argument("--workers", default="auto")
    crew_run.add_argument("--mode", choices=("auto", "quick", "standard", "full"), default="auto")
    crew_run.add_argument("--spawn-policy", choices=("dynamic", "static"), default="dynamic")
    crew_run.add_argument("--seed-contract", required=False)
    crew_run.add_argument("--verification-command", action="append", default=[])
    crew_run.add_argument("--max-rounds", type=int, default=3)
    crew_run.add_argument("--poll-interval", type=float, default=1800.0)
    crew_run.add_argument("--poll-retries", type=int, default=3)
    crew_run.add_argument("--allow-dirty-base", action="store_true")
    crew_status = crew_subparsers.add_parser("status", help="Show crew status")
    crew_status.add_argument("--repo", required=True)
    crew_status.add_argument("--crew", required=False)
    crew_verify = crew_subparsers.add_parser("verify", help="Run crew verification")
    crew_verify.add_argument("--repo", required=True)
    crew_verify.add_argument("--crew", required=False)
    crew_verify.add_argument("--worker", required=False)
    crew_verify.add_argument("--command", required=True)
    crew_challenge = crew_subparsers.add_parser("challenge", help="Record a crew challenge")
    crew_challenge.add_argument("--repo", required=True)
    crew_challenge.add_argument("--crew", required=False)
    crew_challenge.add_argument("--task", required=False)
    crew_challenge.add_argument("--summary", required=True)
    crew_accept = crew_subparsers.add_parser("accept", help="Accept a crew")
    crew_accept.add_argument("--repo", required=True)
    crew_accept.add_argument("--crew", required=False)
    crew_accept.add_argument("--summary", required=True)
    crew_accept.add_argument("--verification-command", action="append", default=[])
    crew_stop = crew_subparsers.add_parser("stop", help="Stop all native Claude sessions for a crew")
    crew_stop.add_argument("--repo", required=True)
    crew_stop.add_argument("--crew", required=False)
    crew_changes = crew_subparsers.add_parser("changes", help="Record worker changed files")
    crew_changes.add_argument("--repo", required=True)
    crew_changes.add_argument("--crew", required=False)
    crew_changes.add_argument("--worker", required=True)
    crew_event_store_health = crew_subparsers.add_parser("event-store-health", help="Show V4 event-store health")
    crew_event_store_health.add_argument("--repo", required=True)
    crew_worker = crew_subparsers.add_parser("worker", help="Operate a crew worker")
    crew_worker_subparsers = crew_worker.add_subparsers(dest="crew_worker_command", required=True)
    for command_name in ("status", "stop"):
        command = crew_worker_subparsers.add_parser(command_name, help=f"{command_name} a crew worker")
        command.add_argument("--repo", required=True)
        command.add_argument("--crew", required=False)
        command.add_argument("--worker", required=True)
        if command_name == "stop":
            command.add_argument("--workspace-cleanup", choices=("keep", "remove"), default="keep")

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


def build_crew_controller(repo_root: Path) -> CrewController:
    state_root = repo_root / ".orchestrator"
    recorder = CrewRecorder(state_root)
    event_store = build_v4_event_store(repo_root, readonly=False)
    blackboard = BlackboardStore(recorder, event_store=event_store)
    worktree_manager = WorktreeManager(state_root)
    return CrewController(
        recorder=recorder,
        blackboard=blackboard,
        task_graph=TaskGraphPlanner(),
        worker_pool=WorkerPool(
            recorder=recorder,
            blackboard=blackboard,
            worktree_manager=worktree_manager,
            native_session=NativeClaudeSession(open_terminal_on_start=True),
            event_store=event_store,
        ),
        verification_runner=CrewVerificationRunner(
            repo_root=repo_root,
            recorder=recorder,
            policy_gate=PolicyGate(),
        ),
        change_recorder=WorkerChangeRecorder(recorder, worktree_manager=worktree_manager),
        merge_arbiter=MergeArbiter(),
        event_store=event_store,
    )


def build_v4_merge_transaction(
    repo_root: Path,
    recorder: CrewRecorder,
    controller: CrewController,
) -> V4MergeTransaction:
    return V4MergeTransaction(
        repo_root=repo_root,
        recorder=recorder,
        event_store=build_v4_event_store(repo_root, readonly=False),
        stop_workers=controller.stop_workers_for_accept,
    )


def build_v4_crew_runner(repo_root: Path, controller: CrewController, poll_timeout: float = 1800.0, poll_retries: int = 3) -> V4CrewRunner:
    recorder = CrewRecorder(repo_root / ".orchestrator")
    message_bus = AgentMessageBus(recorder)
    protocol_store = ProtocolRequestStore(recorder)
    event_store = build_v4_event_store(repo_root, readonly=False)
    supervisor = V4Supervisor(
        event_store=event_store,
        artifact_store=ArtifactStore(repo_root / ".orchestrator" / "v4" / "artifacts"),
        adapter=ClaudeCodeTmuxAdapter(
            native_session=NativeClaudeSession(open_terminal_on_start=False),
            poll_timeout=poll_timeout,
            poll_retries=poll_retries,
        ),
        turn_context_builder=TurnContextBuilder(
            message_bus,
            protocol_request_store=protocol_store,
        ),
        message_ack_processor=MessageAckProcessor(
            event_store=event_store,
            message_bus=message_bus,
        ),
        repo_root=repo_root,
    )
    return V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=event_store,
    )


def build_worker_selection_policy() -> WorkerSelectionPolicy:
    return WorkerSelectionPolicy()


def parse_worker_roles(value: str) -> list[WorkerRole]:
    return [WorkerRole(item.strip()) for item in value.split(",") if item.strip()]


def run_doctor(registry: AgentRegistry) -> dict[str, object]:
    python_ok = sys.version_info >= (3, 11)
    claude_path = shutil.which("claude")
    tmux_path = shutil.which("tmux")
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
        "tmux": {
            "ok": tmux_path is not None,
            "path": tmux_path,
        },
        "agents": [profile.to_dict() for profile in registry.list_profiles()],
    }


def handle_init() -> int:
    """Generate .mcp.json for Claude Code integration."""
    mcp_json = Path(".mcp.json")
    if mcp_json.exists():
        print(f"✓ .mcp.json already exists in {Path.cwd()}")
        print("  To reconfigure, delete it and run 'acr init' again.")
        return 0

    config = {
        "mcpServers": {
            "adversarial-code-review": {
                "command": "acr-mcp"
            }
        }
    }
    mcp_json.write_text(json.dumps(config, indent=2) + "\n")
    print(f"✓ Created .mcp.json in {Path.cwd()}")
    print()
    print("  Next steps:")
    print("  1. Restart Claude Code to load the MCP server")
    print("  2. Use crew_run() tool to start adversarial code review")
    return 0


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
        result = run_doctor(registry)
        print(json.dumps(result, ensure_ascii=False))
        all_ok = result["python"]["ok"] and result["claude_cli"]["ok"] and result["tmux"]["ok"]
        return 0 if all_ok else 1

    if root_command == "init":
        return handle_init()

    if root_command == "crew":
        return handle_crew_command(args)

    if root_command == "runs":
        recorder = RunRecorder(Path(args.repo).resolve() / ".orchestrator")
        if args.run_command == "list":
            print(json.dumps({"runs": recorder.list_runs()}, ensure_ascii=False))
            return 0
        if args.run_command == "show":
            print(json.dumps(recorder.read_run(args.run_id), ensure_ascii=False))
            return 0
        raise ValueError(f"Unsupported runs command: {args.run_command}")

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
        ("crew_command", "crew"),
    ):
        if getattr(args, attr, None) is not None:
            return command
    return args.command


def handle_crew_command(args) -> int:
    repo_root = Path(args.repo).resolve()
    if args.crew_command == "event-store-health":
        if not repo_root.exists():
            raise ValueError(f"repo does not exist: {repo_root}")
        event_store = build_v4_event_store(repo_root, readonly=True)
        print(json.dumps(event_store.health(), ensure_ascii=False))
        return 0

    controller = build_crew_controller(repo_root)
    recorder = CrewRecorder(repo_root / ".orchestrator")
    if args.crew_command == "start":
        if args.spawn_policy == "dynamic" and args.workers == "auto":
            crew = controller.start_dynamic(repo_root=repo_root, goal=args.goal)
            payload = {**crew.to_dict(), "spawn_policy": "dynamic", "seed_contract": args.seed_contract}
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        selection = build_worker_selection_policy().select(goal=args.goal, workers=args.workers, mode=args.mode)
        crew = controller.start(
            repo_root=repo_root,
            goal=args.goal,
            worker_roles=selection.roles,
            allow_dirty_base=args.allow_dirty_base,
        )
        payload = {**crew.to_dict(), **selection.to_dict()}
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if args.crew_command == "run":
        runner = build_v4_crew_runner(repo_root, controller, poll_timeout=args.poll_interval, poll_retries=args.poll_retries)
        if args.spawn_policy == "dynamic" and args.workers == "auto":
            result = runner.run(
                repo_root=repo_root,
                goal=args.goal,
                verification_commands=args.verification_command,
                max_rounds=args.max_rounds,
                poll_interval_seconds=args.poll_interval,
                allow_dirty_base=args.allow_dirty_base,
                spawn_policy="dynamic",
                seed_contract=args.seed_contract,
            )
            print(json.dumps({**result, "spawn_policy": "dynamic", "seed_contract": args.seed_contract}, ensure_ascii=False))
            return 0
        selection = build_worker_selection_policy().select(goal=args.goal, workers=args.workers, mode=args.mode)
        result = runner.run(
            repo_root=repo_root,
            goal=args.goal,
            worker_roles=selection.roles,
            verification_commands=args.verification_command,
            max_rounds=args.max_rounds,
            poll_interval_seconds=args.poll_interval,
            allow_dirty_base=args.allow_dirty_base,
            spawn_policy="static",
        )
        print(
            json.dumps(
                {**result, **selection.to_dict(), "spawn_policy": "static"},
                ensure_ascii=False,
            )
        )
        return 0

    crew_id = args.crew or recorder.latest_crew_id()
    if not crew_id:
        raise ValueError("no crew id provided and no latest crew exists")

    if args.crew_command == "status":
        event_store = build_v4_event_store(repo_root, readonly=True)
        v4_events = event_store.list_stream(crew_id)
        if v4_events:
            from codex_claude_orchestrator.v4.crew_state_projection import CrewStateProjection

            proj = CrewStateProjection.from_events(v4_events)
            if proj.has_events():
                print(
                    json.dumps(
                        {"runtime": "v4", **proj.to_read_crew_dict()},
                        ensure_ascii=False,
                    )
                )
                return 0
        print(json.dumps(controller.status(repo_root=repo_root, crew_id=crew_id), ensure_ascii=False))
        return 0
    if args.crew_command == "verify":
        print(json.dumps(controller.verify(crew_id=crew_id, command=args.command, worker_id=args.worker), ensure_ascii=False))
        return 0
    if args.crew_command == "challenge":
        print(json.dumps(controller.challenge(crew_id=crew_id, task_id=args.task, summary=args.summary), ensure_ascii=False))
        return 0
    if args.crew_command == "accept":
        transaction = build_v4_merge_transaction(repo_root, recorder, controller)
        print(
            json.dumps(
                transaction.accept(
                    crew_id=crew_id,
                    summary=args.summary,
                    verification_commands=args.verification_command,
                ),
                ensure_ascii=False,
            )
        )
        return 0
    if args.crew_command == "stop":
        print(json.dumps(controller.stop(repo_root=repo_root, crew_id=crew_id), ensure_ascii=False))
        return 0
    if args.crew_command == "changes":
        print(json.dumps(controller.changes(crew_id=crew_id, worker_id=args.worker), ensure_ascii=False))
        return 0
    if args.crew_command == "worker":
        if args.crew_worker_command == "status":
            payload = controller.status_worker(repo_root=repo_root, crew_id=crew_id, worker_id=args.worker)
        elif args.crew_worker_command == "stop":
            payload = controller.stop_worker(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=args.worker,
                workspace_cleanup=args.workspace_cleanup,
            )
        else:
            raise ValueError(f"Unsupported crew worker command: {args.crew_worker_command}")
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    raise ValueError(f"Unsupported crew command: {args.crew_command}")
