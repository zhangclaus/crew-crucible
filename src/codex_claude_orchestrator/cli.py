import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.agent_registry import AgentRegistry
from codex_claude_orchestrator.models import TaskRecord, WorkspaceMode
from codex_claude_orchestrator.policy_gate import PolicyGate
from codex_claude_orchestrator.prompt_compiler import PromptCompiler
from codex_claude_orchestrator.result_evaluator import ResultEvaluator
from codex_claude_orchestrator.run_recorder import RunRecorder
from codex_claude_orchestrator.supervisor import Supervisor
from codex_claude_orchestrator.workspace_manager import WorkspaceManager


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

    subparsers.add_parser("doctor", help="Check local orchestrator prerequisites")
    return parser


def build_supervisor(state_root: Path) -> Supervisor:
    return Supervisor(
        prompt_compiler=PromptCompiler(),
        workspace_manager=WorkspaceManager(state_root),
        adapter=ClaudeCliAdapter(),
        policy_gate=PolicyGate(),
        run_recorder=RunRecorder(state_root),
        result_evaluator=ResultEvaluator(),
    )


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
    registry = AgentRegistry.default()

    if args.command == "agents":
        if args.agent_command == "list":
            print(json.dumps({"agents": [profile.to_dict() for profile in registry.list_profiles()]}, ensure_ascii=False))
            return 0
        raise ValueError(f"Unsupported agents command: {args.agent_command}")

    if args.command == "doctor":
        print(json.dumps(run_doctor(registry), ensure_ascii=False))
        return 0

    if args.command == "runs":
        recorder = RunRecorder(Path(args.repo).resolve() / ".orchestrator")
        if args.run_command == "list":
            print(json.dumps({"runs": recorder.list_runs()}, ensure_ascii=False))
            return 0
        if args.run_command == "show":
            print(json.dumps(recorder.read_run(args.run_id), ensure_ascii=False))
            return 0
        raise ValueError(f"Unsupported runs command: {args.run_command}")

    if args.command != "dispatch":
        raise ValueError(f"Unsupported command: {args.command}")

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
