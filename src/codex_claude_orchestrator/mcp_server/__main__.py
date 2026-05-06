from __future__ import annotations

import asyncio
import os
from pathlib import Path


def _build_controller():
    """从环境变量构建 CrewController。"""
    from codex_claude_orchestrator.crew.controller import CrewController
    from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
    from codex_claude_orchestrator.state.blackboard import BlackboardStore
    from codex_claude_orchestrator.workers.pool import WorkerPool
    from codex_claude_orchestrator.runtime.native_claude_session import NativeClaudeSession
    from codex_claude_orchestrator.crew.task_graph import TaskGraphPlanner
    from codex_claude_orchestrator.workspace.worktree_manager import WorktreeManager
    from codex_claude_orchestrator.v4.event_store_factory import build_v4_event_store

    repo = Path(os.environ.get("CREW_REPO", "."))
    state_root = repo / ".orchestrator"
    recorder = CrewRecorder(state_root)
    event_store = build_v4_event_store(repo, readonly=False)
    blackboard = BlackboardStore(recorder, event_store=event_store)
    session = NativeClaudeSession()
    worktree_manager = WorktreeManager(state_root)
    pool = WorkerPool(
        recorder=recorder,
        blackboard=blackboard,
        worktree_manager=worktree_manager,
        native_session=session,
        event_store=event_store,
    )
    controller = CrewController(
        recorder=recorder,
        blackboard=blackboard,
        worker_pool=pool,
        task_graph=TaskGraphPlanner(),
        event_store=event_store,
    )
    return controller


async def main() -> None:
    from codex_claude_orchestrator.mcp_server.server import create_server

    controller = _build_controller()
    server = create_server(controller=controller)
    await server.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
