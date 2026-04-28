import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
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


def default_allowed_tools(workspace_mode: WorkspaceMode, shared_write_allowed: bool) -> list[str]:
    read_tools = ["Read", "Glob", "Grep", "LS"]
    write_tools = ["Edit", "MultiEdit", "Write", "Bash"]
    if workspace_mode is WorkspaceMode.READONLY:
        return read_tools
    if workspace_mode is WorkspaceMode.SHARED and not shared_write_allowed:
        return read_tools
    return read_tools + write_tools


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "dispatch":
        raise ValueError(f"Unsupported command: {args.command}")

    repo_root = Path(args.repo).resolve()
    supervisor = build_supervisor(repo_root / ".orchestrator")
    workspace_mode = WorkspaceMode(args.workspace_mode)
    task = TaskRecord(
        task_id=args.task_id or f"task-{uuid4()}",
        parent_task_id=None,
        origin="cli",
        assigned_agent=args.assigned_agent,
        goal=args.goal,
        task_type="adhoc",
        scope=str(repo_root),
        workspace_mode=workspace_mode,
        allowed_tools=default_allowed_tools(workspace_mode, args.allow_shared_write),
        shared_write_allowed=args.allow_shared_write,
    )
    outcome = supervisor.dispatch(task, repo_root)
    print(json.dumps(outcome.to_dict(), ensure_ascii=False))
    return 0
