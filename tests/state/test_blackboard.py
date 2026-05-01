from pathlib import Path

from codex_claude_orchestrator.state.blackboard import BlackboardStore
from codex_claude_orchestrator.crew.models import ActorType, BlackboardEntry, BlackboardEntryType, CrewRecord
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder


def test_blackboard_appends_and_filters_entries(tmp_path: Path):
    recorder = CrewRecorder(tmp_path / ".orchestrator")
    recorder.start_crew(CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo="/repo"))
    blackboard = BlackboardStore(recorder)
    blackboard.append(
        BlackboardEntry(
            entry_id="entry-1",
            crew_id="crew-1",
            task_id="task-explorer",
            actor_type=ActorType.CODEX,
            actor_id="codex",
            type=BlackboardEntryType.DECISION,
            content="Start explorer first.",
            confidence=1.0,
        )
    )

    assert blackboard.list_entries("crew-1")[0]["entry_id"] == "entry-1"
    assert blackboard.list_entries("crew-1", entry_type=BlackboardEntryType.DECISION)[0]["content"] == (
        "Start explorer first."
    )
    assert blackboard.list_entries("crew-1", task_id="missing") == []
