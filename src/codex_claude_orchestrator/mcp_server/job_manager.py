"""Background job manager for non-blocking crew_run with delta status tracking."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Job:
    job_id: str
    status: str = "running"  # running / done / failed / cancelled
    phase: str = "idle"  # spawning / polling / reviewing / verifying / idle
    current_round: int = 0
    max_rounds: int = 0
    elapsed_seconds: float = 0.0
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    thread: threading.Thread | None = None
    completed_at: float | None = None
    subtasks: list[dict[str, Any]] | None = None  # parallel mode subtask tracking
    failure_context: dict[str, Any] | None = None  # failure details from max_rounds_exhausted
    _start_time: float = field(default_factory=time.monotonic, repr=False)

    # Delta tracking
    last_reported_phase: str = ""
    last_reported_round: int = 0

    def has_changed(self) -> bool:
        """Whether state has changed since last report."""
        return (
            self.phase != self.last_reported_phase
            or self.current_round != self.last_reported_round
        )

    def mark_reported(self) -> None:
        """Mark current state as reported."""
        self.last_reported_phase = self.phase
        self.last_reported_round = self.current_round

    def update_elapsed(self) -> None:
        self.elapsed_seconds = time.monotonic() - self._start_time


def _next_poll_seconds(job: Job) -> int:
    """Adaptive poll interval based on elapsed time."""
    elapsed = job.elapsed_seconds
    if elapsed < 5:
        return 5
    if elapsed < 15:
        return 10
    if elapsed < 35:
        return 20
    if elapsed < 75:
        return 40
    return 60


_MAX_CONCURRENT_JOBS = 8


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()

    def create_job(
        self,
        *,
        runner,
        repo_root: Path,
        goal: str,
        crew_id: str = "",
        verification_commands: list[str] | None = None,
        max_rounds: int = 3,
        parallel: bool = False,
        max_workers: int = 3,
        subtasks: list[dict[str, str]] | None = None,
        long_task: bool = False,
        supervisor_mode: bool = False,
    ) -> str:
        """Create a job, start background thread, return job_id."""
        if self._shutdown_event.is_set():
            raise RuntimeError("JobManager is shutting down, cannot create new jobs")
        with self._lock:
            running = sum(1 for j in self._jobs.values() if j.completed_at is None)
            if running >= _MAX_CONCURRENT_JOBS:
                raise RuntimeError(f"too many concurrent jobs ({running}/{_MAX_CONCURRENT_JOBS})")

        job_id = f"job-{uuid.uuid4().hex[:8]}"
        job = Job(
            job_id=job_id,
            max_rounds=max_rounds,
        )

        def _run() -> None:
            try:
                if supervisor_mode:
                    _start_supervisor_agent(
                        repo_root=repo_root,
                        goal=goal,
                        crew_id=crew_id or f"crew-{job_id}",
                        verification_commands=verification_commands or [],
                        max_rounds=max_rounds,
                        job_id=job_id,
                        job_manager=self,
                    )
                    return
                if parallel:
                    import asyncio
                    if subtasks:
                        from codex_claude_orchestrator.v4.subtask import SubTask
                        parsed = [SubTask.from_dict(s) for s in subtasks]
                    else:
                        parsed = _split_goal_into_subtasks(goal)
                    result = asyncio.run(runner.async_supervise(
                        repo_root=repo_root,
                        crew_id=crew_id or f"crew-{job_id}",
                        goal=goal,
                        subtasks=parsed,
                        verification_commands=verification_commands or [],
                        max_rounds=max_rounds,
                        max_workers=max_workers,
                        progress_callback=lambda phase, round_idx, _max: self._on_progress(
                            job_id, phase, round_idx
                        ),
                        cancel_event=job.cancel_event,
                    ))
                elif crew_id:
                    result = runner.supervise(
                        repo_root=repo_root,
                        crew_id=crew_id,
                        verification_commands=verification_commands or [],
                        max_rounds=max_rounds,
                        progress_callback=lambda phase, round_idx, _max: self._on_progress(
                            job_id, phase, round_idx
                        ),
                        cancel_event=job.cancel_event,
                    )
                else:
                    run_kwargs: dict[str, Any] = dict(
                        repo_root=repo_root,
                        goal=goal,
                        verification_commands=verification_commands or [],
                        max_rounds=max_rounds,
                        progress_callback=lambda phase, round_idx, _max: self._on_progress(
                            job_id, phase, round_idx
                        ),
                        cancel_event=job.cancel_event,
                    )
                    if long_task:
                        run_kwargs["long_task"] = True
                    result = runner.run(**run_kwargs)
                with self._lock:
                    if job.status != "cancelled":
                        job.status = "done"
                    job.result = result  # always store, even if cancelled
                    # Extract failure_context if present (from max_rounds_exhausted)
                    if isinstance(result, dict) and result.get("failure_context"):
                        job.failure_context = result["failure_context"]
                    job.phase = "idle"
            except Exception as exc:
                with self._lock:
                    if job.status != "cancelled":
                        job.status = "failed"
                        job.error = str(exc)
                    job.phase = "idle"
            finally:
                with self._lock:
                    job.completed_at = time.monotonic()
                    job.update_elapsed()

        thread = threading.Thread(target=_run, daemon=True, name=f"crew-job-{job_id}")
        job.thread = thread

        with self._lock:
            self._jobs[job_id] = job

        thread.start()
        return job_id

    def _on_progress(self, job_id: str, phase: str, round_index: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.phase = phase
            job.current_round = round_index
            job.update_elapsed()

    def get_job(self, job_id: str) -> dict[str, Any]:
        """Return a snapshot dict of the job state."""
        with self._lock:
            self._evict_stale()
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"job not found: {job_id}")
            job.update_elapsed()
            return {
                "job_id": job.job_id,
                "status": job.status,
                "phase": job.phase,
                "current_round": job.current_round,
                "max_rounds": job.max_rounds,
                "elapsed_seconds": job.elapsed_seconds,
                "result": job.result,
                "error": job.error,
                "cancel_event": job.cancel_event,
                "completed_at": job.completed_at,
                "subtasks": job.subtasks,
                "failure_context": job.failure_context,
            }

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        """Return serialized job status snapshot, all under lock.

        Returns a dict with the job's current state. The caller does not need
        to hold the lock or worry about TOCTOU races.
        """
        with self._lock:
            self._evict_stale()
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"job not found: {job_id}")
            job.update_elapsed()
            return {
                "job_id": job.job_id,
                "status": job.status,
                "phase": job.phase,
                "current_round": job.current_round,
                "max_rounds": job.max_rounds,
                "elapsed_seconds": job.elapsed_seconds,
                "result": job.result,
                "error": job.error,
                "has_changed": (
                    job.phase != job.last_reported_phase
                    or job.current_round != job.last_reported_round
                ),
                "subtasks": job.subtasks,
                "failure_context": job.failure_context,
            }

    def get_status_and_mark_reported(self, job_id: str) -> dict[str, Any]:
        """Return serialized job status snapshot AND mark as reported, atomically."""
        with self._lock:
            self._evict_stale()
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"job not found: {job_id}")
            job.update_elapsed()
            result = {
                "job_id": job.job_id,
                "status": job.status,
                "phase": job.phase,
                "current_round": job.current_round,
                "max_rounds": job.max_rounds,
                "elapsed_seconds": job.elapsed_seconds,
                "result": job.result,
                "error": job.error,
                "has_changed": (
                    job.phase != job.last_reported_phase
                    or job.current_round != job.last_reported_round
                ),
                "subtasks": job.subtasks,
                "failure_context": job.failure_context,
            }
            # Atomically mark as reported
            job.last_reported_phase = job.phase
            job.last_reported_round = job.current_round
            return result

    def update_job_subtasks(self, job_id: str, subtasks: list[dict[str, Any]]) -> None:
        """Update subtask status for a parallel job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.subtasks = subtasks

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job. Returns True if cancellation was initiated, False if already terminal."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"job not found: {job_id}")
            if job.status == "running":
                job.cancel_event.set()
                job.status = "cancelled"
                return True
            return False

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            self._evict_stale()
            result = []
            for job in self._jobs.values():
                job.update_elapsed()
                result.append(
                    {
                        "job_id": job.job_id,
                        "status": job.status,
                        "phase": job.phase,
                        "current_round": job.current_round,
                        "max_rounds": job.max_rounds,
                        "elapsed": round(job.elapsed_seconds),
                    }
                )
            return result

    def _evict_stale(self) -> None:
        """Remove completed jobs older than 1 hour."""
        cutoff = time.monotonic() - 3600
        stale = [
            jid for jid, j in self._jobs.items()
            if j.status in ("done", "failed", "cancelled")
            and j.completed_at is not None
            and j.completed_at < cutoff
        ]
        for jid in stale:
            del self._jobs[jid]

    def shutdown(self, timeout: float = 5.0) -> None:
        """Cancel all running jobs and join all background threads.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._shutdown_event.is_set():
            return
        self._shutdown_event.set()

        # Single critical section: cancel + collect threads
        threads = []
        with self._lock:
            for job in self._jobs.values():
                if job.status == "running":
                    job.cancel_event.set()
                    job.status = "cancelled"
                if job.thread is not None:
                    threads.append(job.thread)

        for thread in threads:
            thread.join(timeout=timeout)


def _split_goal_into_subtasks(goal: str) -> list:
    """Simple default task splitting. Returns a single subtask for the whole goal."""
    from codex_claude_orchestrator.v4.subtask import SubTask
    return [
        SubTask(
            task_id="st-1",
            description=goal,
            scope=["src/", "tests/"],
        )
    ]


def _start_supervisor_agent(
    *,
    repo_root: Path,
    goal: str,
    crew_id: str,
    verification_commands: list[str],
    max_rounds: int,
    job_id: str,
    job_manager: "JobManager",
) -> None:
    """Start a Claude CLI supervisor agent that directly controls workers via MCP tools."""
    import importlib.resources
    import json
    import shutil
    import shlex
    import subprocess

    claude = shutil.which("claude") or "claude"
    tmux = shutil.which("tmux") or "tmux"

    # Bug 3 fix: load skill from package resources
    try:
        skill_content = importlib.resources.files(
            "codex_claude_orchestrator.skills"
        ).joinpath("orchestration-default.md").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        skill_content = "You are a crew supervisor. Coordinate worker agents to complete the task."

    prompt = (
        f"You are a crew supervisor for the following task:\n\n"
        f"Goal: {goal}\n"
        f"Crew ID: {crew_id}\n"
        f"Repo: {repo_root}\n"
        f"Verification commands: {', '.join(verification_commands) or 'none'}\n"
        f"Max rounds: {max_rounds}\n\n"
        f"{skill_content}\n\n"
        f"When the task is complete, call crew_accept(crew_id='{crew_id}') and report the result."
    )

    # Bug 2 fix: write prompt to temp file to avoid shell injection
    prompt_dir = repo_root / ".orchestrator"
    prompt_dir.mkdir(exist_ok=True)
    prompt_path = prompt_dir / f"supervisor-prompt-{job_id}.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    # Bug 1 fix: pass .mcp.json to claude CLI so it has MCP tools
    mcp_config = repo_root / ".mcp.json"
    session = f"crew-supervisor-{job_id}"

    # Build claude command
    claude_cmd = [claude, "--dangerously-skip-permissions"]
    if mcp_config.exists():
        claude_cmd.extend(["--mcp-config", str(mcp_config)])
    claude_cmd.extend(["-p", f"$(cat {shlex.quote(str(prompt_path))})"])

    # Bug 6 fix: start tmux session and open Terminal.app
    subprocess.run([tmux, "new-session", "-d", "-s", session, "-c", str(repo_root)], check=False)
    subprocess.run(
        [tmux, "send-keys", "-t", f"{session}:0", shlex.join(claude_cmd), "C-m"],
        check=False,
    )

    # Open Terminal.app and attach to tmux session
    shell_command = shlex.join([tmux, "attach", "-t", session])
    subprocess.run([
        "osascript",
        "-e", 'tell application "Terminal"',
        "-e", "activate",
        "-e", f"do script {json.dumps(shell_command, ensure_ascii=False)}",
        "-e", "end tell",
    ], check=False)

    # Bug 4+5 fix: poll tmux session for completion with cancel support
    job = job_manager._jobs[job_id]

    def _poll_supervisor_loop() -> None:
        while True:
            if job.cancel_event.is_set():
                subprocess.run([tmux, "kill-session", "-t", session], check=False)
                # Clean up prompt file
                prompt_path.unlink(missing_ok=True)
                with job._lock:
                    job.status = "cancelled"
                    job.phase = "idle"
                    job.completed_at = time.monotonic()
                return
            result = subprocess.run(
                [tmux, "has-session", "-t", session],
                capture_output=True, check=False,
            )
            if result.returncode != 0:
                # tmux session ended
                prompt_path.unlink(missing_ok=True)
                with job._lock:
                    if job.status != "cancelled":
                        job.status = "done"
                    job.phase = "idle"
                    job.completed_at = time.monotonic()
                return
            time.sleep(5)

    poll_thread = threading.Thread(target=_poll_supervisor_loop, daemon=True, name=f"supervisor-poll-{job_id}")
    poll_thread.start()
