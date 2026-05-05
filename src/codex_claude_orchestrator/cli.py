import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.session.agent_registry import AgentRegistry
from codex_claude_orchestrator.bridge.supervisor_loop import BridgeSupervisorLoop
from codex_claude_orchestrator.state.blackboard import BlackboardStore
from codex_claude_orchestrator.bridge.claude_bridge import ClaudeBridge
from codex_claude_orchestrator.runtime.claude_window import ClaudeWindowLauncher
from codex_claude_orchestrator.crew.controller import CrewController
from codex_claude_orchestrator.crew.models import WorkerRole
from codex_claude_orchestrator.messaging.message_bus import AgentMessageBus
from codex_claude_orchestrator.messaging.protocol_requests import ProtocolRequestStore
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.crew.supervisor_loop import CrewSupervisorLoop
from codex_claude_orchestrator.verification.crew_runner import CrewVerificationRunner
from codex_claude_orchestrator.crew.merge_arbiter import MergeArbiter
from codex_claude_orchestrator.core.models import TaskRecord, WorkspaceMode
from codex_claude_orchestrator.runtime.native_claude_session import NativeClaudeSession
from codex_claude_orchestrator.core.policy_gate import PolicyGate
from codex_claude_orchestrator.session.prompt_compiler import PromptCompiler
from codex_claude_orchestrator.verification.result_evaluator import ResultEvaluator
from codex_claude_orchestrator.state.run_recorder import RunRecorder
from codex_claude_orchestrator.session.engine import SessionEngine
from codex_claude_orchestrator.state.session_recorder import SessionRecorder
from codex_claude_orchestrator.session.skill_evolution import SkillEvolution
from codex_claude_orchestrator.session.supervisor import Supervisor
from codex_claude_orchestrator.crew.task_graph import TaskGraphPlanner
from codex_claude_orchestrator.runtime.tmux_console import TmuxCommandRunner, TmuxConsole, build_default_term_name
from codex_claude_orchestrator.ui.server import run_ui_server
from codex_claude_orchestrator.v4.adapters.tmux_claude import ClaudeCodeTmuxAdapter
from codex_claude_orchestrator.v4.artifacts import ArtifactStore
from codex_claude_orchestrator.v4.crew_runner import V4CrewRunner
from codex_claude_orchestrator.v4.event_store_factory import build_v4_event_store
from codex_claude_orchestrator.v4.merge_transaction import V4MergeTransaction
from codex_claude_orchestrator.v4.message_ack import MessageAckProcessor
from codex_claude_orchestrator.v4.projections import CrewProjection
from codex_claude_orchestrator.v4.supervisor import V4Supervisor
from codex_claude_orchestrator.v4.turn_context import TurnContextBuilder
from codex_claude_orchestrator.verification.runner import VerificationRunner
from codex_claude_orchestrator.workers.change_recorder import WorkerChangeRecorder
from codex_claude_orchestrator.workers.pool import WorkerPool
from codex_claude_orchestrator.workers.selection import WorkerSelectionPolicy
from codex_claude_orchestrator.workspace.worktree_manager import WorktreeManager
from codex_claude_orchestrator.workspace.manager import WorkspaceManager


BUILTIN_CAPABILITIES = [
    "inspect_code",
    "edit_source",
    "edit_tests",
    "review_patch",
    "run_verification",
    "browser_e2e",
    "research_external",
    "write_docs",
    "design_architecture",
    "triage_failure",
    "maintain_guardrails",
]


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
    crew_run.add_argument("--verification-command", action="append", required=True)
    crew_run.add_argument("--max-rounds", type=int, default=3)
    crew_run.add_argument("--poll-interval", type=float, default=5.0)
    crew_run.add_argument("--allow-dirty-base", action="store_true")
    crew_run.add_argument("--legacy-loop", action="store_true")
    crew_status = crew_subparsers.add_parser("status", help="Show crew status")
    crew_status.add_argument("--repo", required=True)
    crew_status.add_argument("--crew", required=False)
    crew_blackboard = crew_subparsers.add_parser("blackboard", help="Show crew blackboard")
    crew_blackboard.add_argument("--repo", required=True)
    crew_blackboard.add_argument("--crew", required=False)
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
    crew_prune = crew_subparsers.add_parser("prune", help="Prune orphaned crew tmux sessions")
    crew_prune.add_argument("--repo", required=True)
    crew_changes = crew_subparsers.add_parser("changes", help="Record worker changed files")
    crew_changes.add_argument("--repo", required=True)
    crew_changes.add_argument("--crew", required=False)
    crew_changes.add_argument("--worker", required=True)
    crew_merge_plan = crew_subparsers.add_parser("merge-plan", help="Build a crew merge plan")
    crew_merge_plan.add_argument("--repo", required=True)
    crew_merge_plan.add_argument("--crew", required=False)
    crew_supervise = crew_subparsers.add_parser("supervise", help="Reserved for a local crew supervisor loop")
    crew_supervise.add_argument("--repo", required=True)
    crew_supervise.add_argument("--crew", required=False)
    crew_supervise.add_argument("--verification-command", action="append", required=True)
    crew_supervise.add_argument("--max-rounds", type=int, default=3)
    crew_supervise.add_argument("--poll-interval", type=float, default=5.0)
    crew_supervise.add_argument("--dynamic", action="store_true")
    crew_supervise.add_argument("--legacy-loop", action="store_true")
    crew_contracts = crew_subparsers.add_parser("contracts", help="List dynamic worker contracts")
    crew_contracts.add_argument("--repo", required=True)
    crew_contracts.add_argument("--crew", required=False)
    crew_messages = crew_subparsers.add_parser("messages", help="List crew message bus entries")
    crew_messages.add_argument("--repo", required=True)
    crew_messages.add_argument("--crew", required=False)
    crew_events = crew_subparsers.add_parser("events", help="List V4 crew events")
    crew_events.add_argument("--repo", required=True)
    crew_events.add_argument("--crew", required=True)
    crew_event_store_health = crew_subparsers.add_parser("event-store-health", help="Show V4 event-store health")
    crew_event_store_health.add_argument("--repo", required=True)
    crew_inbox = crew_subparsers.add_parser("inbox", help="Read a worker inbox")
    crew_inbox.add_argument("--repo", required=True)
    crew_inbox.add_argument("--crew", required=False)
    crew_inbox.add_argument("--worker", required=True)
    crew_protocols = crew_subparsers.add_parser("protocols", help="List protocol request state changes")
    crew_protocols.add_argument("--repo", required=True)
    crew_protocols.add_argument("--crew", required=False)
    crew_decisions = crew_subparsers.add_parser("decisions", help="List dynamic decision actions")
    crew_decisions.add_argument("--repo", required=True)
    crew_decisions.add_argument("--crew", required=False)
    crew_snapshot = crew_subparsers.add_parser("snapshot", help="Show team snapshot")
    crew_snapshot.add_argument("--repo", required=True)
    crew_snapshot.add_argument("--crew", required=False)
    crew_resume_context = crew_subparsers.add_parser("resume-context", help="Show replay context for resuming supervision")
    crew_resume_context.add_argument("--repo", required=True)
    crew_resume_context.add_argument("--crew", required=False)
    crew_capabilities = crew_subparsers.add_parser("capabilities", help="Inspect capability vocabulary")
    crew_capability_subparsers = crew_capabilities.add_subparsers(dest="crew_capability_command", required=True)
    crew_capability_list = crew_capability_subparsers.add_parser("list", help="List builtin capabilities")
    crew_capability_list.add_argument("--repo", required=True)
    crew_capability_show = crew_capability_subparsers.add_parser("show", help="Show a builtin capability")
    crew_capability_show.add_argument("--repo", required=True)
    crew_capability_show.add_argument("--capability", required=True)
    crew_worker = crew_subparsers.add_parser("worker", help="Operate a crew worker")
    crew_worker_subparsers = crew_worker.add_subparsers(dest="crew_worker_command", required=True)
    for command_name in ("send", "observe", "attach", "tail", "status", "stop"):
        command = crew_worker_subparsers.add_parser(command_name, help=f"{command_name} a crew worker")
        command.add_argument("--repo", required=True)
        command.add_argument("--crew", required=False)
        command.add_argument("--worker", required=True)
        if command_name == "send":
            command.add_argument("--message", required=True)
        if command_name == "observe":
            command.add_argument("--lines", type=int, default=200)
        if command_name == "tail":
            command.add_argument("--limit", type=int, default=80)
        if command_name == "stop":
            command.add_argument("--workspace-cleanup", choices=("keep", "remove"), default="keep")

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
    claude_bridge_supervise.add_argument("--verification-command", action="append", required=True)
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
    claude_bridge_run.add_argument("--verification-command", action="append", required=True)
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


def build_crew_controller(repo_root: Path) -> CrewController:
    state_root = repo_root / ".orchestrator"
    recorder = CrewRecorder(state_root)
    blackboard = BlackboardStore(recorder)
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
        ),
        verification_runner=CrewVerificationRunner(
            repo_root=repo_root,
            recorder=recorder,
            policy_gate=PolicyGate(),
        ),
        change_recorder=WorkerChangeRecorder(recorder, worktree_manager=worktree_manager),
        merge_arbiter=MergeArbiter(),
    )


def build_crew_supervisor_loop(controller: CrewController) -> CrewSupervisorLoop:
    return CrewSupervisorLoop(controller=controller)


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


def build_v4_crew_runner(repo_root: Path, controller: CrewController) -> V4CrewRunner:
    recorder = CrewRecorder(repo_root / ".orchestrator")
    message_bus = AgentMessageBus(recorder)
    protocol_store = ProtocolRequestStore(recorder)
    event_store = build_v4_event_store(repo_root, readonly=False)
    supervisor = V4Supervisor(
        event_store=event_store,
        artifact_store=ArtifactStore(repo_root / ".orchestrator" / "v4" / "artifacts"),
        adapter=ClaudeCodeTmuxAdapter(
            native_session=NativeClaudeSession(open_terminal_on_start=False),
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

    if root_command == "crew":
        return handle_crew_command(args)

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
        from codex_claude_orchestrator.core.models import SkillStatus

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
        ("crew_command", "crew"),
    ):
        if getattr(args, attr, None) is not None:
            return command
    return args.command


def handle_crew_command(args) -> int:
    repo_root = Path(args.repo).resolve()
    if args.crew_command == "events":
        if not repo_root.exists():
            raise ValueError(f"repo does not exist: {repo_root}")
        event_store = build_v4_event_store(repo_root, readonly=True)
        print(json.dumps([event.to_dict() for event in event_store.list_stream(args.crew)], ensure_ascii=False))
        return 0
    if args.crew_command == "event-store-health":
        if not repo_root.exists():
            raise ValueError(f"repo does not exist: {repo_root}")
        event_store = build_v4_event_store(repo_root, readonly=True)
        print(json.dumps(event_store.health(), ensure_ascii=False))
        return 0

    controller = build_crew_controller(repo_root)
    recorder = CrewRecorder(repo_root / ".orchestrator")
    if args.crew_command == "capabilities":
        if args.crew_capability_command == "list":
            print(json.dumps({"capabilities": BUILTIN_CAPABILITIES}, ensure_ascii=False))
            return 0
        if args.crew_capability_command == "show":
            if args.capability not in BUILTIN_CAPABILITIES:
                raise ValueError(f"unknown capability: {args.capability}")
            print(
                json.dumps(
                    {
                        "capability": args.capability,
                        "available": True,
                        "summary": f"Builtin dynamic worker capability: {args.capability}",
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        raise ValueError(f"Unsupported crew capabilities command: {args.crew_capability_command}")

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
        runner = build_crew_supervisor_loop(controller) if args.legacy_loop else build_v4_crew_runner(repo_root, controller)
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

    if args.crew_command == "prune":
        print(json.dumps(controller.prune_orphans(repo_root=repo_root), ensure_ascii=False))
        return 0

    crew_id = args.crew or recorder.latest_crew_id()
    if not crew_id:
        raise ValueError("no crew id provided and no latest crew exists")

    if args.crew_command == "status":
        event_store = build_v4_event_store(repo_root, readonly=True)
        v4_events = event_store.list_stream(crew_id)
        if v4_events:
            projection = CrewProjection.from_events(v4_events)
            print(
                json.dumps(
                    {
                        "runtime": "v4",
                        "crew": {
                            "crew_id": projection.crew_id,
                            "root_goal": projection.goal,
                            "status": projection.status,
                        },
                        "projection": projection.to_dict(),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        print(json.dumps(controller.status(repo_root=repo_root, crew_id=crew_id), ensure_ascii=False))
        return 0
    if args.crew_command == "blackboard":
        print(json.dumps({"blackboard": controller.blackboard_entries(crew_id=crew_id)}, ensure_ascii=False))
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
    if args.crew_command == "merge-plan":
        print(json.dumps(controller.merge_plan(crew_id=crew_id), ensure_ascii=False))
        return 0
    if args.crew_command == "supervise":
        runner = build_crew_supervisor_loop(controller) if args.legacy_loop else build_v4_crew_runner(repo_root, controller)
        if args.dynamic and args.legacy_loop:
            print(
                json.dumps(
                    {
                        **runner.supervise_dynamic(
                            repo_root=repo_root,
                            crew_id=crew_id,
                            verification_commands=args.verification_command,
                            max_rounds=args.max_rounds,
                            poll_interval_seconds=args.poll_interval,
                        ),
                        "spawn_policy": "dynamic",
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        print(
            json.dumps(
                runner.supervise(
                    repo_root=repo_root,
                    crew_id=crew_id,
                    verification_commands=args.verification_command,
                    max_rounds=args.max_rounds,
                    poll_interval_seconds=args.poll_interval,
                    **({"dynamic": True} if not args.legacy_loop and args.dynamic else {}),
                ),
                ensure_ascii=False,
            )
        )
        return 0
    if args.crew_command == "contracts":
        print(json.dumps({"contracts": recorder.read_crew(crew_id)["worker_contracts"]}, ensure_ascii=False))
        return 0
    if args.crew_command == "messages":
        print(json.dumps({"messages": recorder.read_crew(crew_id)["messages"]}, ensure_ascii=False))
        return 0
    if args.crew_command == "inbox":
        from codex_claude_orchestrator.messaging.message_bus import AgentMessageBus

        print(
            json.dumps(
                {"messages": AgentMessageBus(recorder).read_inbox(crew_id=crew_id, recipient=args.worker, mark_read=True)},
                ensure_ascii=False,
            )
        )
        return 0
    if args.crew_command == "protocols":
        print(json.dumps({"protocol_requests": recorder.read_crew(crew_id)["protocol_requests"]}, ensure_ascii=False))
        return 0
    if args.crew_command == "decisions":
        print(json.dumps({"decisions": recorder.read_crew(crew_id)["decisions"]}, ensure_ascii=False))
        return 0
    if args.crew_command == "snapshot":
        print(json.dumps({"team_snapshot": recorder.read_crew(crew_id)["team_snapshot"]}, ensure_ascii=False))
        return 0
    if args.crew_command == "resume-context":
        print(json.dumps(controller.resume_context(crew_id=crew_id), ensure_ascii=False))
        return 0
    if args.crew_command == "worker":
        if args.crew_worker_command == "send":
            payload = controller.send_worker(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=args.worker,
                message=args.message,
            )
        elif args.crew_worker_command == "observe":
            payload = controller.observe_worker(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=args.worker,
                lines=args.lines,
            )
        elif args.crew_worker_command == "attach":
            payload = controller.attach_worker(repo_root=repo_root, crew_id=crew_id, worker_id=args.worker)
        elif args.crew_worker_command == "tail":
            payload = controller.tail_worker(repo_root=repo_root, crew_id=crew_id, worker_id=args.worker, limit=args.limit)
        elif args.crew_worker_command == "status":
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
