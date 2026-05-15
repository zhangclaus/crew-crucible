"""Tests for HistoryManager — per-turn result files and index.md management."""

import json
from pathlib import Path

import pytest

from codex_claude_orchestrator.workers.history_manager import HistoryManager


class TestHistoryManagerInit:
    """Tests for HistoryManager initialization."""

    def test_init_creates_history_dir(self, tmp_path: Path) -> None:
        """HistoryManager creates .crew-history/ in work_dir."""
        work_dir = tmp_path / "my-project"
        work_dir.mkdir()

        manager = HistoryManager(work_dir=work_dir)

        assert manager.history_dir.is_dir()
        assert manager.history_dir == work_dir / ".crew-history"


class TestSaveTurnResult:
    """Tests for save_turn_result."""

    def test_save_turn_result_creates_file(self, tmp_path: Path) -> None:
        """Saves JSON to turn-N-result.json and returns the path."""
        manager = HistoryManager(work_dir=tmp_path)
        result = {"status": "completed", "output": "done"}

        path = manager.save_turn_result(turn_number=3, result=result)

        assert path == tmp_path / ".crew-history" / "turn-3-result.json"
        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == result


class TestUpdateIndex:
    """Tests for update_index."""

    def test_update_index_creates_and_updates(self, tmp_path: Path) -> None:
        """Creates index.md with header, then appends rows."""
        manager = HistoryManager(work_dir=tmp_path)

        manager.update_index(
            turn_number=1,
            task="Fix bug",
            status="completed",
            summary="Fixed the bug in auth module",
            changed_files=["src/auth.py", "tests/test_auth.py"],
        )

        content = manager.index_path.read_text(encoding="utf-8")
        assert "# Crew Work History" in content
        assert "| Turn |" in content
        assert "Fix bug" in content
        assert "Fixed the bug in auth module" in content
        assert "src/auth.py" in content
        assert "tests/test_auth.py" in content

        # Append a second row
        manager.update_index(
            turn_number=2,
            task="Add tests",
            status="completed",
            summary="Added unit tests",
            changed_files=["tests/test_new.py"],
        )

        content = manager.index_path.read_text(encoding="utf-8")
        assert content.count("| 1 |") == 1
        assert content.count("| 2 |") == 1

    def test_update_index_tracks_status(self, tmp_path: Path) -> None:
        """Shows completed/failed status in the index."""
        manager = HistoryManager(work_dir=tmp_path)

        manager.update_index(
            turn_number=1,
            task="Good task",
            status="completed",
            summary="It worked",
            changed_files=[],
        )
        manager.update_index(
            turn_number=2,
            task="Bad task",
            status="failed",
            summary="It broke",
            changed_files=[],
        )

        content = manager.index_path.read_text(encoding="utf-8")
        assert "completed" in content
        assert "failed" in content


class TestListTurns:
    """Tests for list_turns."""

    def test_list_turns_returns_sorted_numbers(self, tmp_path: Path) -> None:
        """Returns [1, 2, 3] for files saved out of order."""
        manager = HistoryManager(work_dir=tmp_path)

        # Save out of order
        for n in [3, 1, 2]:
            manager.save_turn_result(turn_number=n, result={"turn": n})

        turns = manager.list_turns()
        assert turns == [1, 2, 3]


class TestReadTurnResult:
    """Tests for read_turn_result."""

    def test_read_turn_result_returns_dict(self, tmp_path: Path) -> None:
        """Reads back a previously saved result."""
        manager = HistoryManager(work_dir=tmp_path)
        result = {"status": "ok", "data": [1, 2, 3]}
        manager.save_turn_result(turn_number=5, result=result)

        loaded = manager.read_turn_result(turn_number=5)
        assert loaded == result

    def test_read_turn_result_returns_none_when_missing(self, tmp_path: Path) -> None:
        """Returns None for a turn that doesn't exist."""
        manager = HistoryManager(work_dir=tmp_path)
        assert manager.read_turn_result(turn_number=99) is None


class TestTruncate:
    """Tests for _truncate helper."""

    def test_truncate_short_text_unchanged(self) -> None:
        from codex_claude_orchestrator.workers.history_manager import _truncate

        assert _truncate("hello", 10) == "hello"

    def test_truncate_long_text_with_suffix(self) -> None:
        from codex_claude_orchestrator.workers.history_manager import _truncate

        result = _truncate("hello world", 8)
        assert result == "hello..."
        assert len(result) == 8

    def test_truncate_exact_length(self) -> None:
        from codex_claude_orchestrator.workers.history_manager import _truncate

        assert _truncate("exact", 5) == "exact"
