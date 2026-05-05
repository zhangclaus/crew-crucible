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

    repo = Path(os.environ.get("CREW_REPO", "."))
    state_root = repo / ".orchestrator"
    recorder = CrewRecorder(state_root)
    blackboard = BlackboardStore(recorder)
    session = NativeClaudeSession()
    worktree_manager = WorktreeManager(state_root)
    pool = WorkerPool(
        recorder=recorder,
        blackboard=blackboard,
        worktree_manager=worktree_manager,
        native_session=session,
    )
    controller = CrewController(
        recorder=recorder,
        blackboard=blackboard,
        worker_pool=pool,
        task_graph=TaskGraphPlanner(),
    )
    return controller


async def main() -> None:
    from codex_claude_orchestrator.mcp_server.server import create_server
    from codex_claude_orchestrator.crew.supervisor_loop import CrewSupervisorLoop

    controller = _build_controller()
    supervision_loop = CrewSupervisorLoop(controller=controller)

    server = create_server(controller=controller, supervision_loop=supervision_loop)
    await server.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
