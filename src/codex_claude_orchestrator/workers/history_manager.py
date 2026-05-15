"""Per-turn result files and index.md management for worker context."""

from __future__ import annotations

import json
from pathlib import Path


def _truncate(text: str, max_len: int) -> str:
    """Truncate *text* to at most *max_len* characters, appending '...' if trimmed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


class HistoryManager:
    """Manages per-turn result files and an ``index.md`` for lazy-loaded worker context.

    Each turn's result is stored as ``turn-N-result.json`` inside a
    ``.crew-history/`` directory relative to *work_dir*.  An ``index.md``
    file in the same directory provides a quick overview of all turns.
    """

    def __init__(self, *, work_dir: Path) -> None:
        self._work_dir = work_dir
        self._history_dir = work_dir / ".crew-history"
        self._history_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def history_dir(self) -> Path:
        """Return the path to the ``.crew-history/`` directory."""
        return self._history_dir

    @property
    def index_path(self) -> Path:
        """Return the path to ``index.md`` inside the history directory."""
        return self._history_dir / "index.md"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_turn_result(self, *, turn_number: int, result: dict) -> Path:
        """Persist *result* as ``turn-N-result.json`` and return the file path."""
        path = self._history_dir / f"turn-{turn_number}-result.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def update_index(
        self,
        *,
        turn_number: int,
        task: str,
        status: str,
        summary: str,
        changed_files: list[str],
    ) -> None:
        """Append a row to ``index.md``.  Creates the file with a header if needed."""
        if not self.index_path.exists():
            self._write_index_header()

        files_str = ", ".join(changed_files) if changed_files else "-"
        task_truncated = _truncate(task, 60)
        summary_truncated = _truncate(summary, 80)

        row = (
            f"| {turn_number} "
            f"| {task_truncated} "
            f"| {status} "
            f"| {summary_truncated} "
            f"| {files_str} |\n"
        )

        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(row)

    def list_turns(self) -> list[int]:
        """Return a sorted list of turn numbers discovered from result files."""
        turns: list[int] = []
        for p in self._history_dir.glob("turn-*-result.json"):
            # Filename format: turn-N-result.json
            name = p.stem  # turn-N-result
            parts = name.split("-")
            # parts == ["turn", "N", "result"]
            if len(parts) == 3 and parts[0] == "turn" and parts[2] == "result":
                try:
                    turns.append(int(parts[1]))
                except ValueError:
                    continue
        return sorted(turns)

    def read_turn_result(self, turn_number: int) -> dict | None:
        """Read and return the result dict for *turn_number*, or ``None`` if missing."""
        path = self._history_dir / f"turn-{turn_number}-result.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_index_header(self) -> None:
        """Write the markdown table header to ``index.md``."""
        header = (
            "# Crew Work History\n"
            "\n"
            "| Turn | Task | Status | Summary | Changed Files |\n"
            "|------|------|--------|---------|---------------|\n"
        )
        self.index_path.write_text(header, encoding="utf-8")
