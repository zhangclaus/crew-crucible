from __future__ import annotations

from codex_claude_orchestrator.crew.models import BlackboardEntry, BlackboardEntryType
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder


class BlackboardStore:
    def __init__(self, recorder: CrewRecorder):
        self._recorder = recorder

    def append(self, entry: BlackboardEntry) -> None:
        self._recorder.append_blackboard(entry.crew_id, entry)

    def list_entries(
        self,
        crew_id: str,
        *,
        entry_type: BlackboardEntryType | None = None,
        task_id: str | None = None,
    ) -> list[dict]:
        entries = self._recorder.read_crew(crew_id)["blackboard"]
        if entry_type is not None:
            entries = [entry for entry in entries if entry.get("type") == entry_type.value]
        if task_id is not None:
            entries = [entry for entry in entries if entry.get("task_id") == task_id]
        return entries
