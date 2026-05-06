from __future__ import annotations

from codex_claude_orchestrator.crew.models import BlackboardEntry, BlackboardEntryType
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.v4.domain_events import DomainEventEmitter


class BlackboardStore:
    def __init__(self, recorder: CrewRecorder, *, event_store=None):
        self._recorder = recorder
        self._domain_events = DomainEventEmitter(event_store) if event_store else None

    def append(self, entry: BlackboardEntry) -> None:
        self._recorder.append_blackboard(entry.crew_id, entry)
        if self._domain_events:
            entry_type = entry.type.value if hasattr(entry.type, "value") else str(entry.type)
            self._domain_events.emit_blackboard_entry(
                entry.crew_id, entry.entry_id, entry_type, entry.content,
            )

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
