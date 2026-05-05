from __future__ import annotations

import json
from pathlib import Path

from codex_claude_orchestrator.v4.event_stream import FilesystemRuntimeEventStream


def test_filesystem_event_stream_dedupes_outbox_and_tails_transcript(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox.json"
    transcript = tmp_path / "transcript.txt"
    state = tmp_path / "stream-state.json"
    outbox.write_text(
        json.dumps(
            {
                "crew_id": "crew-1",
                "worker_id": "worker-1",
                "turn_id": "turn-1",
                "status": "completed",
                "summary": "done",
            }
        ),
        encoding="utf-8",
    )
    transcript.write_text("first chunk\nmarker-1\n", encoding="utf-8")
    stream = FilesystemRuntimeEventStream(state_path=state)

    first = stream.poll_once(
        crew_id="crew-1",
        turn_id="turn-1",
        worker_id="worker-1",
        outbox_path=outbox,
        transcript_path=transcript,
        expected_marker="marker-1",
        outbox_artifact_ref="workers/worker-1/outbox/turn-1.json",
        transcript_artifact_ref="workers/worker-1/transcript.txt",
    )
    second = stream.poll_once(
        crew_id="crew-1",
        turn_id="turn-1",
        worker_id="worker-1",
        outbox_path=outbox,
        transcript_path=transcript,
        expected_marker="marker-1",
        outbox_artifact_ref="workers/worker-1/outbox/turn-1.json",
        transcript_artifact_ref="workers/worker-1/transcript.txt",
    )

    assert [event.type for event in first] == [
        "worker.outbox.detected",
        "runtime.output.appended",
        "marker.detected",
    ]
    assert second == []


def test_filesystem_event_stream_resumes_transcript_cursor_across_instances(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.txt"
    state = tmp_path / "stream-state.json"
    transcript.write_text("first\n", encoding="utf-8")
    FilesystemRuntimeEventStream(state_path=state).poll_once(
        crew_id="crew-1",
        turn_id="turn-1",
        worker_id="worker-1",
        transcript_path=transcript,
        expected_marker="marker-1",
        transcript_artifact_ref="workers/worker-1/transcript.txt",
    )
    transcript.write_text("first\nsecond\n", encoding="utf-8")

    events = FilesystemRuntimeEventStream(state_path=state).poll_once(
        crew_id="crew-1",
        turn_id="turn-1",
        worker_id="worker-1",
        transcript_path=transcript,
        expected_marker="marker-1",
        transcript_artifact_ref="workers/worker-1/transcript.txt",
    )

    assert [event.type for event in events] == ["runtime.output.appended"]
    assert events[0].payload["text"] == "second\n"


def test_filesystem_event_stream_ignores_malformed_state_values(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.txt"
    state = tmp_path / "stream-state.json"
    transcript.write_text("fresh\n", encoding="utf-8")
    state.write_text(
        json.dumps(
            {
                "outbox_sha256": {"bad": ["not", "a", "hash"]},
                "transcript_offsets": {f"turn-1:{transcript.resolve()}": ["bad"]},
            }
        ),
        encoding="utf-8",
    )

    events = FilesystemRuntimeEventStream(state_path=state).poll_once(
        crew_id="crew-1",
        turn_id="turn-1",
        worker_id="worker-1",
        transcript_path=transcript,
    )

    assert [event.type for event in events] == ["runtime.output.appended"]
    assert events[0].payload["text"] == "fresh\n"


def test_filesystem_event_stream_can_commit_after_durable_append(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox.json"
    state = tmp_path / "stream-state.json"
    outbox.write_text(
        json.dumps(
            {
                "crew_id": "crew-1",
                "worker_id": "worker-1",
                "turn_id": "turn-1",
                "status": "completed",
                "summary": "done",
            }
        ),
        encoding="utf-8",
    )
    stream = FilesystemRuntimeEventStream(state_path=state)

    first = stream.poll_once(
        crew_id="crew-1",
        turn_id="turn-1",
        worker_id="worker-1",
        outbox_path=outbox,
        autocommit=False,
    )
    second = FilesystemRuntimeEventStream(state_path=state).poll_once(
        crew_id="crew-1",
        turn_id="turn-1",
        worker_id="worker-1",
        outbox_path=outbox,
        autocommit=False,
    )
    stream.commit_events(first)
    third = FilesystemRuntimeEventStream(state_path=state).poll_once(
        crew_id="crew-1",
        turn_id="turn-1",
        worker_id="worker-1",
        outbox_path=outbox,
        autocommit=False,
    )

    assert [event.type for event in first] == ["worker.outbox.detected"]
    assert [event.type for event in second] == ["worker.outbox.detected"]
    assert third == []
