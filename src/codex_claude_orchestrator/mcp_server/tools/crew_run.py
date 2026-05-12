"""Non-blocking crew_run, delta crew_status, and crew_cancel MCP tools."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from codex_claude_orchestrator.mcp_server.job_manager import JobManager, _next_poll_seconds

# Per-repo runner cache to avoid rebuilding for repeated crew_run calls on the same repo.
_MAX_RUNNER_CACHE = 16


class _BoundedRunnerCache(dict):
    """dict subclass that evicts the oldest entry when size exceeds _MAX_RUNNER_CACHE."""

    def __setitem__(self, key, value):
        if len(self) >= _MAX_RUNNER_CACHE and key not in self:
            oldest_key = next(iter(self))
            del self[oldest_key]
        super().__setitem__(key, value)


_runner_cache: dict[str, object] = _BoundedRunnerCache()


def _build_terminal_response(snap: dict) -> dict:
    """Build response dict for terminal job states (done/failed/cancelled)."""
    base = {
        "job_id": snap["job_id"],
        "status": snap["status"],
        "elapsed": round(snap["elapsed_seconds"]),
        "rounds": snap["current_round"],
    }
    if snap["status"] == "done" and snap["result"] is not None:
        base["result"] = snap["result"]
    if snap["status"] == "failed" and snap.get("error"):
        base["error"] = snap["error"]
    if snap.get("subtasks"):
        base["subtasks"] = snap["subtasks"]
    # Include failure_context when max_rounds_exhausted with verification failures
    failure_context = snap.get("failure_context")
    if failure_context:
        base["failure_details"] = failure_context
    return base


def register_run_tools(
    server: FastMCP,
    controller,
    job_manager: JobManager,
    runner=None,
) -> None:

    @server.tool("crew_run")
    async def crew_run(
        repo: str,
        goal: str = "",
        crew_id: str = "",
        verification_commands: list[str] | None = None,
        max_rounds: int = 3,
        parallel: bool = False,
        max_workers: int = 3,
        subtasks: list[dict[str, str]] | None = None,
        long_task: bool = False,
    ) -> list[TextContent]:
        """Start a crew job in the background (non-blocking).

        Returns job_id + background_agent_prompt. The caller should spawn a
        background agent (Agent tool with run_in_background=true) using the
        returned prompt to handle polling, so the main session context stays
        clean. The background agent will poll crew_job_status and call
        crew_accept when done.

        Args:
            repo: Repository root path
            goal: Task goal description
            crew_id: Existing crew ID (optional)
            verification_commands: Commands to verify completion
            max_rounds: Maximum supervision rounds
            parallel: Enable parallel worker mode (default False)
            max_workers: Max concurrent workers in parallel mode (default 3)
            subtasks: Optional list of subtask dicts with keys task_id,
                description, scope (list of paths). When provided with
                parallel=True, these are used instead of the default
                single-subtask split.
            long_task: Enable long task mode with multi-stage execution
                (default False). When True, delegates to LongTaskSupervisor.
        """
        # Clamp resource limits
        max_workers = min(max(int(max_workers), 1), 5)
        max_rounds = min(max(int(max_rounds), 1), 10)

        if runner is None:
            if repo not in _runner_cache:
                if len(_runner_cache) >= _MAX_RUNNER_CACHE:
                    # Evict oldest entry
                    oldest_key = next(iter(_runner_cache))
                    del _runner_cache[oldest_key]
                _runner_cache[repo] = _build_runner(controller, repo)
            cached_runner = _runner_cache[repo]
        else:
            cached_runner = runner

        job_id = job_manager.create_job(
            runner=cached_runner,
            repo_root=Path(repo),
            goal=goal,
            crew_id=crew_id,
            verification_commands=verification_commands,
            max_rounds=max_rounds,
            parallel=parallel,
            max_workers=max_workers,
            subtasks=subtasks,
            long_task=long_task,
        )

        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "job_id": job_id,
                        "status": "running",
                        "poll_hint": f"5秒后调 crew_job_status('{job_id}')",
                        "poll_after_seconds": 5,
                        "background_agent_prompt": (
                            f"你是一个后台任务监控 agent。你的职责：\n"
                            f"1. 每隔 poll_after_seconds 秒调用 crew_job_status('{job_id}')\n"
                            f"2. 如果返回 status='running'，记录 phase/round，按返回的 poll_after_seconds 等待后重试\n"
                            f"3. 如果返回 status='unchanged'，按返回的 elapsed 推算等待时间后重试\n"
                            f"4. 如果返回 status='done'，从 result 中取 crew_id 字段，调用 crew_accept(crew_id) 然后报告最终结果\n"
                            f"5. 如果返回 status='failed' 或 'cancelled'，报告错误\n"
                            f"6. 如果返回 status='done' 但 result.status='max_rounds_exhausted'，检查 failure_details 字段获取失败原因（last_verification.output），报告给主会话以便决定是否修复后重试\n\n"
                            f"注意：你拥有独立上下文，轮询结果不会污染主会话。持续轮询直到终态。"
                        ),
                    },
                    ensure_ascii=False,
                ),
            )
        ]

    @server.tool("crew_job_status")
    async def crew_job_status(job_id: str) -> list[TextContent]:
        """Poll crew job status. Returns delta (only changes) to minimize context usage."""
        try:
            snap = job_manager.get_status_and_mark_reported(job_id)
        except KeyError:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"error": f"job not found: {job_id}"}),
                )
            ]

        status = snap["status"]
        elapsed = round(snap["elapsed_seconds"])

        # Terminal states: return full result
        if status in ("done", "failed", "cancelled"):
            return [
                TextContent(
                    type="text",
                    text=json.dumps(_build_terminal_response(snap), ensure_ascii=False),
                )
            ]

        # Running: delta mode
        if not snap["has_changed"]:
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "job_id": snap["job_id"],
                            "status": "unchanged",
                            "elapsed": elapsed,
                        }
                    ),
                )
            ]

        # State changed — return meaningful info and mark reported (atomically)
        snap = job_manager.get_status_and_mark_reported(job_id)
        elapsed = round(snap["elapsed_seconds"])
        status = snap["status"]

        # Re-check terminal after atomic mark (background thread may have completed)
        if status in ("done", "failed", "cancelled"):
            return [
                TextContent(
                    type="text",
                    text=json.dumps(_build_terminal_response(snap), ensure_ascii=False),
                )
            ]

        # Build a temporary Job-like object for _next_poll_seconds
        class _Snap:
            elapsed_seconds = snap["elapsed_seconds"]

        delta = {
            "job_id": snap["job_id"],
            "status": "running",
            "phase": snap["phase"],
            "round": snap["current_round"],
            "elapsed": elapsed,
            "poll_after_seconds": _next_poll_seconds(_Snap()),
        }
        if snap.get("subtasks"):
            delta["subtasks"] = snap["subtasks"]

        return [
            TextContent(
                type="text",
                text=json.dumps(delta,
                ),
            )
        ]

    @server.tool("crew_cancel")
    async def crew_cancel(job_id: str) -> list[TextContent]:
        """Cancel a running crew job."""
        try:
            cancelled = job_manager.cancel_job(job_id)
        except KeyError:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"error": f"job not found: {job_id}"}),
                )
            ]
        if not cancelled:
            try:
                job = job_manager.get_job(job_id)
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"job_id": job_id, "status": job["status"], "warning": "job already terminal"}),
                    )
                ]
            except KeyError:
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"job_id": job_id, "status": "unknown", "error": "job evicted during cancel"}),
                    )
                ]
        return [
            TextContent(
                type="text",
                text=json.dumps({"job_id": job_id, "status": "cancelling"}),
            )
        ]


def _build_runner(controller, repo: str):
    """Build a V4CrewRunner with the controller's dependencies."""
    from codex_claude_orchestrator.v4.crew_runner import V4CrewRunner
    from codex_claude_orchestrator.v4.supervisor import V4Supervisor
    from codex_claude_orchestrator.v4.adapters.tmux_claude import ClaudeCodeTmuxAdapter
    from codex_claude_orchestrator.v4.artifacts import ArtifactStore
    from codex_claude_orchestrator.v4.event_store_factory import build_v4_event_store
    from codex_claude_orchestrator.runtime.native_claude_session import NativeClaudeSession

    repo_path = Path(repo)
    event_store = build_v4_event_store(repo_path, readonly=False)
    artifact_store = ArtifactStore(repo_path / ".orchestrator" / "artifacts")
    adapter = ClaudeCodeTmuxAdapter(
        native_session=NativeClaudeSession(),
    )
    supervisor = V4Supervisor(
        event_store=event_store,
        artifact_store=artifact_store,
        adapter=adapter,
        repo_root=repo_path,
    )
    return V4CrewRunner(
        controller=controller,
        supervisor=supervisor,
        event_store=event_store,
    )
