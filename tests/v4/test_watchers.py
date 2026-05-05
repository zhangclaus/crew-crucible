from __future__ import annotations

import json
from pathlib import Path

from codex_claude_orchestrator.v4.watchers import (
    MarkerDetector,
    OutboxWatcher,
    ProcessWatcher,
    TimeoutWatcher,
    TranscriptTailWatcher,
)


def test_outbox_watcher_emits_evidence_not_terminal_turn_event(tmp_path: Path) -> None:
    outbox = tmp_path / "turn-1.json"
    outbox.write_text(
        json.dumps(
            {
                "crew_id": "crew-1",
                "worker_id": "worker-1",
                "turn_id": "turn-1",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )

    events = list(
        OutboxWatcher().watch(
            crew_id="crew-1",
            turn_id="turn-1",
            worker_id="worker-1",
            outbox_path=outbox,
            artifact_ref="workers/worker-1/outbox/turn-1.json",
        )
    )

    assert [event.type for event in events] == ["worker.outbox.detected"]
    assert events[0].payload["valid"] is True
    assert events[0].artifact_refs == ["workers/worker-1/outbox/turn-1.json"]


def test_outbox_watcher_preserves_verification_evidence(tmp_path: Path) -> None:
    outbox = tmp_path / "turn-1.json"
    outbox.write_text(
        json.dumps(
            {
                "crew_id": "crew-1",
                "worker_id": "worker-1",
                "turn_id": "turn-1",
                "status": "completed",
                "verification": [
                    {
                        "command": "pytest tests/v4 -q",
                        "status": "passed",
                        "summary": "V4 tests passed.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    events = list(
        OutboxWatcher().watch(
            crew_id="crew-1",
            turn_id="turn-1",
            worker_id="worker-1",
            outbox_path=outbox,
            artifact_ref="workers/worker-1/outbox/turn-1.json",
        )
    )

    assert events[0].payload["verification"] == [
        {
            "command": "pytest tests/v4 -q",
            "status": "passed",
            "summary": "V4 tests passed.",
        }
    ]


def test_outbox_watcher_preserves_typed_review_verdict(tmp_path: Path) -> None:
    outbox = tmp_path / "turn-review.json"
    outbox.write_text(
        json.dumps(
            {
                "crew_id": "crew-1",
                "worker_id": "worker-review",
                "turn_id": "turn-review",
                "status": "completed",
                "review": {
                    "verdict": "warn",
                    "summary": "small risk remains",
                    "findings": ["manual edge case not covered"],
                    "evidence_refs": ["review.json"],
                },
            }
        ),
        encoding="utf-8",
    )

    events = list(
        OutboxWatcher().watch(
            crew_id="crew-1",
            turn_id="turn-review",
            worker_id="worker-review",
            outbox_path=outbox,
            artifact_ref="workers/worker-review/outbox/turn-review.json",
        )
    )

    assert events[0].payload["review"]["verdict"] == "warn"


def test_outbox_watcher_rejects_mismatched_identity(tmp_path: Path) -> None:
    outbox = tmp_path / "turn-1.json"
    outbox.write_text(
        json.dumps(
            {
                "crew_id": "crew-1",
                "worker_id": "other-worker",
                "turn_id": "turn-1",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )

    events = list(
        OutboxWatcher().watch(
            crew_id="crew-1",
            turn_id="turn-1",
            worker_id="worker-1",
            outbox_path=outbox,
            artifact_ref="workers/worker-1/outbox/turn-1.json",
        )
    )

    assert events[0].payload["valid"] is False
    assert "worker_id does not match watched worker" in events[0].payload["validation_errors"]


def test_outbox_watcher_reports_invalid_outbox_as_evidence(tmp_path: Path) -> None:
    outbox = tmp_path / "turn-1.json"
    outbox.write_text("{bad json", encoding="utf-8")

    events = list(OutboxWatcher().watch(turn_id="turn-1", worker_id="worker-1", outbox_path=outbox))

    assert [event.type for event in events] == ["worker.outbox.detected"]
    assert events[0].payload["valid"] is False
    assert "error" in events[0].payload
    assert not Path(events[0].artifact_refs[0]).is_absolute()


def test_transcript_tail_watcher_emits_incremental_output(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("hello\nworld\n", encoding="utf-8")

    events, offset = TranscriptTailWatcher().watch(
        turn_id="turn-1",
        worker_id="worker-1",
        transcript_path=transcript,
        offset=0,
        artifact_ref="workers/worker-1/transcript.txt",
    )

    assert [event.type for event in events] == ["runtime.output.appended"]
    assert events[0].payload["text"] == "hello\nworld\n"
    assert events[0].artifact_refs == ["workers/worker-1/transcript.txt"]
    assert offset == len("hello\nworld\n".encode("utf-8"))


def test_marker_detector_emits_marker_evidence_only() -> None:
    events = list(
        MarkerDetector().detect(
            turn_id="turn-1",
            worker_id="worker-1",
            text="done TURN_DONE",
            expected_marker="TURN_DONE",
        )
    )

    assert [event.type for event in events] == ["marker.detected"]


def test_process_and_timeout_watchers_emit_raw_evidence() -> None:
    process_events = list(
        ProcessWatcher().process_exited(
            turn_id="turn-1",
            worker_id="worker-1",
            reason="pane closed",
        )
    )
    timeout_events = list(
        TimeoutWatcher().deadline_reached(
            turn_id="turn-1",
            worker_id="worker-1",
            deadline_at="2026-05-02T00:00:00Z",
        )
    )

    assert [event.type for event in process_events] == ["runtime.process_exited"]
    assert [event.type for event in timeout_events] == ["turn.deadline_reached"]
