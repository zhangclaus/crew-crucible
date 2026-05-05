"""Evidence watchers for V4 runtime turns."""

from __future__ import annotations

import json
from pathlib import Path

from codex_claude_orchestrator.v4.outbox import WorkerOutboxResult
from codex_claude_orchestrator.v4.runtime import RuntimeEvent


class TranscriptTailWatcher:
    def watch(
        self,
        *,
        turn_id: str,
        worker_id: str,
        transcript_path: Path,
        offset: int = 0,
        artifact_ref: str | None = None,
    ) -> tuple[list[RuntimeEvent], int]:
        if not transcript_path.exists():
            return [], offset

        data = transcript_path.read_bytes()
        if offset > len(data):
            offset = 0
        chunk = data[offset:]
        next_offset = len(data)
        if not chunk:
            return [], next_offset

        text = chunk.decode("utf-8", errors="replace")
        return [
            RuntimeEvent(
                type="runtime.output.appended",
                turn_id=turn_id,
                worker_id=worker_id,
                payload={"text": text, "offset": offset, "next_offset": next_offset},
                artifact_refs=[_artifact_ref(transcript_path, artifact_ref)],
            )
        ], next_offset


class OutboxWatcher:
    def watch(
        self,
        *,
        turn_id: str,
        worker_id: str,
        outbox_path: Path,
        crew_id: str = "",
        artifact_ref: str | None = None,
    ):
        if not outbox_path.exists():
            return

        try:
            payload = json.loads(outbox_path.read_text(encoding="utf-8"))
            result = WorkerOutboxResult.from_dict(payload)
            validation_errors = list(result.validation_errors)
            if crew_id and result.crew_id != crew_id:
                validation_errors.append("crew_id does not match watched crew")
            if result.worker_id != worker_id:
                validation_errors.append("worker_id does not match watched worker")
            if result.turn_id != turn_id:
                validation_errors.append("turn_id does not match watched turn")
            event_payload = {
                "valid": not validation_errors,
                "status": result.status,
                "summary": result.summary,
                "changed_files": result.changed_files,
                "artifact_refs": result.artifact_refs,
                "verification": result.verification,
                "review": result.review,
                "acknowledged_message_ids": result.acknowledged_message_ids,
                "validation_errors": validation_errors,
            }
        except Exception as exc:
            event_payload = {"valid": False, "error": str(exc)}

        yield RuntimeEvent(
            type="worker.outbox.detected",
            turn_id=turn_id,
            worker_id=worker_id,
            payload=event_payload,
            artifact_refs=[_artifact_ref(outbox_path, artifact_ref)],
        )


class MarkerDetector:
    def detect(
        self,
        *,
        turn_id: str,
        worker_id: str,
        text: str,
        expected_marker: str,
        source: str = "transcript",
        artifact_refs: list[str] | None = None,
    ):
        if expected_marker and expected_marker in text:
            yield RuntimeEvent(
                type="marker.detected",
                turn_id=turn_id,
                worker_id=worker_id,
                payload={"marker": expected_marker, "source": source},
                artifact_refs=list(artifact_refs or []),
            )


class ProcessWatcher:
    def process_exited(self, *, turn_id: str, worker_id: str, reason: str = ""):
        yield RuntimeEvent(
            type="runtime.process_exited",
            turn_id=turn_id,
            worker_id=worker_id,
            payload={"reason": reason},
        )


class TimeoutWatcher:
    def deadline_reached(self, *, turn_id: str, worker_id: str, deadline_at: str):
        yield RuntimeEvent(
            type="turn.deadline_reached",
            turn_id=turn_id,
            worker_id=worker_id,
            payload={"deadline_at": deadline_at},
        )


def _artifact_ref(path: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    return path.as_posix() if not path.is_absolute() else path.name
