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
    thread: threading.Thread | None = None
    completed_at: float | None = None
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


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create_job(
        self,
        *,
        runner,
        repo_root: Path,
        goal: str,
        crew_id: str = "",
        verification_commands: list[str] | None = None,
        max_rounds: int = 3,
    ) -> str:
        """Create a job, start background thread, return job_id."""
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        job = Job(
            job_id=job_id,
            max_rounds=max_rounds,
        )

        def _run() -> None:
            try:
                if crew_id:
                    result = runner.supervise(
                        repo_root=repo_root,
                        crew_id=crew_id,
                        verification_commands=verification_commands or ["echo ok"],
                        max_rounds=max_rounds,
                        progress_callback=lambda phase, round_idx, _max: self._on_progress(
                            job_id, phase, round_idx
                        ),
                        cancel_event=job.cancel_event,
                    )
                else:
                    result = runner.run(
                        repo_root=repo_root,
                        goal=goal,
                        verification_commands=verification_commands or ["echo ok"],
                        max_rounds=max_rounds,
                        progress_callback=lambda phase, round_idx, _max: self._on_progress(
                            job_id, phase, round_idx
                        ),
                        cancel_event=job.cancel_event,
                    )
                with self._lock:
                    if job.status != "cancelled":
                        job.status = "done"
                    job.result = result  # always store, even if cancelled
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

    def get_job(self, job_id: str) -> Job:
        with self._lock:
            self._evict_stale()
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"job not found: {job_id}")
            job.update_elapsed()
            return job

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
            }

    def mark_job_reported(self, job_id: str) -> None:
        """Mark a job's current state as reported (under lock)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.last_reported_phase = job.phase
                job.last_reported_round = job.current_round

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
