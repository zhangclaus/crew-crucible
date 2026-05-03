"""Planner helpers that turn V4 evidence into deterministic routing choices."""

from __future__ import annotations

from typing import Any

from codex_claude_orchestrator.crew.scope import scope_covers_all as _scope_covers_all


_AUTHORITY_ORDER = {
    "readonly": 0,
    "test_write": 1,
    "source_write": 2,
    "state_write": 3,
}


class PlannerPolicy:
    def select_worker(
        self,
        *,
        workers: list[dict[str, Any]],
        required_authority: str,
        required_capabilities: list[str],
        requested_write_scope: list[str],
        worker_quality_scores: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        worker_quality_scores = worker_quality_scores or {}
        candidates = [
            worker
            for worker in workers
            if _is_active(worker)
            and _authority_covers(str(worker.get("authority_level", "readonly")), required_authority)
            and set(required_capabilities).issubset(set(worker.get("capabilities", [])))
            and _scope_covers_all(worker.get("write_scope", []), requested_write_scope)
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda worker: (
                int(worker_quality_scores.get(str(worker.get("worker_id", "")), 0)),
                str(worker.get("worker_id", "")),
            ),
        )


def _is_active(worker: dict[str, Any]) -> bool:
    return worker.get("status", "running") in {"running", "idle"}


def _authority_covers(worker_authority: str, required_authority: str) -> bool:
    return _AUTHORITY_ORDER.get(worker_authority, 0) >= _AUTHORITY_ORDER.get(required_authority, 0)


__all__ = ["PlannerPolicy"]
