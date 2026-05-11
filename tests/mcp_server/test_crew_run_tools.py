"""Tests for crew_run helper functions."""

from __future__ import annotations

import pytest


class TestBuildTerminalResponse:
    def test_extracted_function_exists(self):
        """_build_terminal_response should be a standalone function."""
        from codex_claude_orchestrator.mcp_server.tools.crew_run import _build_terminal_response

        snap = {
            "job_id": "j1",
            "status": "done",
            "elapsed_seconds": 42,
            "current_round": 3,
            "result": {"crew_id": "c1"},
            "error": None,
            "subtasks": None,
        }
        result = _build_terminal_response(snap)
        assert result["status"] == "done"
        assert result["job_id"] == "j1"
        assert result["result"] == {"crew_id": "c1"}

    def test_failed_status_includes_error(self):
        from codex_claude_orchestrator.mcp_server.tools.crew_run import _build_terminal_response

        snap = {
            "job_id": "j2",
            "status": "failed",
            "elapsed_seconds": 10,
            "current_round": 1,
            "result": None,
            "error": "worker crashed",
            "subtasks": None,
        }
        result = _build_terminal_response(snap)
        assert result["status"] == "failed"
        assert result["error"] == "worker crashed"


class TestRunnerCacheEviction:
    def test_cache_does_not_grow_beyond_limit(self):
        """_runner_cache should evict oldest entries when full."""
        from codex_claude_orchestrator.mcp_server.tools import crew_run

        # Save original cache
        original_cache = crew_run._runner_cache.copy()
        try:
            crew_run._runner_cache.clear()

            # Fill cache beyond limit
            for i in range(20):
                crew_run._runner_cache[f"repo-{i}"] = f"runner-{i}"

            # Cache should be bounded (max 16 entries)
            assert len(crew_run._runner_cache) <= 16, (
                f"Cache has {len(crew_run._runner_cache)} entries, expected <= 16"
            )
        finally:
            crew_run._runner_cache.clear()
            crew_run._runner_cache.update(original_cache)
