from pathlib import Path

from codex_claude_orchestrator.crew.models import CrewRecord, CrewStatus, CrewTaskRecord, WorkerRole
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder


def test_crew_recorder_persists_crew_tasks_workers_artifacts_and_final_report(tmp_path: Path):
    recorder = CrewRecorder(tmp_path / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo="/repo")
    task = CrewTaskRecord(
        task_id="task-explorer",
        crew_id=crew.crew_id,
        title="Explore",
        instructions="Read only.",
        role_required=WorkerRole.EXPLORER,
    )

    crew_dir = recorder.start_crew(crew)
    recorder.write_tasks(crew.crew_id, [task])
    artifact = recorder.write_text_artifact(crew.crew_id, "workers/worker-1/allocation.json", "{}")
    recorder.finalize_crew(crew.crew_id, CrewStatus.ACCEPTED, "accepted")
    details = recorder.read_crew(crew.crew_id)

    assert crew_dir == tmp_path / ".orchestrator" / "crews" / "crew-1"
    assert artifact.name == "allocation.json"
    assert details["crew"]["status"] == "accepted"
    assert details["tasks"][0]["task_id"] == "task-explorer"
    assert details["artifacts"] == ["workers/worker-1/allocation.json"]
    assert details["final_report"]["final_summary"] == "accepted"
    assert recorder.latest_crew_id() == "crew-1"


def test_crew_recorder_appends_known_pitfalls_jsonl(tmp_path: Path):
    recorder = CrewRecorder(tmp_path / ".orchestrator")
    crew = CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo="/repo")
    recorder.start_crew(crew)

    pitfall = recorder.append_known_pitfall(
        crew.crew_id,
        failure_class="verification_repeat",
        summary="pytest failed three times on the same command",
        guardrail="Run the focused failing test before broad retries.",
        evidence_refs=["workers/worker-source/verification.json"],
    )
    details = recorder.read_crew(crew.crew_id)

    assert pitfall["failure_class"] == "verification_repeat"
    assert pitfall["guardrail"] == "Run the focused failing test before broad retries."
    assert details["known_pitfalls"] == [pitfall]
