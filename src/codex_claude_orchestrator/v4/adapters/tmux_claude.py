from __future__ import annotations

import json
from pathlib import Path

from codex_claude_orchestrator.v4.event_stream import FilesystemRuntimeEventStream
from codex_claude_orchestrator.v4.runtime import (
    CancellationResult,
    DeliveryResult,
    RuntimeEvent,
    StopResult,
    TurnEnvelope,
    WorkerHandle,
    WorkerSpec,
)


def _non_empty_str(value) -> str:
    return value if isinstance(value, str) and value else ""


def _terminal_pane_for(turn: TurnEnvelope, worker: WorkerSpec | None) -> str:
    return (_non_empty_str(worker.terminal_pane) if worker else "") or turn.worker_id


class ClaudeCodeTmuxAdapter:
    def __init__(self, *, native_session):
        self._native_session = native_session
        self._workers: dict[str, WorkerSpec] = {}

    def register_worker(self, spec: WorkerSpec) -> WorkerHandle:
        self._workers[spec.worker_id] = spec
        return WorkerHandle(
            crew_id=spec.crew_id,
            worker_id=spec.worker_id,
            runtime_type=spec.runtime_type,
        )

    def spawn_worker(self, spec: WorkerSpec) -> WorkerHandle:
        return self.register_worker(spec)

    def deliver_turn(self, turn: TurnEnvelope) -> DeliveryResult:
        worker = self._workers.get(turn.worker_id)
        terminal_pane = _terminal_pane_for(turn, worker)
        self._initialize_filesystem_stream(turn, worker)
        result = self._native_session.send(
            terminal_pane=terminal_pane,
            message=_compiled_turn_message(turn),
            turn_marker=turn.expected_marker,
        )
        marker = _non_empty_str(result.get("marker")) or turn.expected_marker
        reason = _non_empty_str(result.get("reason"))
        if result.get("delivered") is False or result.get("ok") is False:
            return DeliveryResult(
                delivered=False,
                marker=marker,
                reason=reason,
            )
        return DeliveryResult(
            delivered=True,
            marker=marker,
            reason=reason or "sent to tmux pane",
        )

    def watch_turn(self, turn: TurnEnvelope):
        worker = self._workers.get(turn.worker_id)
        terminal_pane = _terminal_pane_for(turn, worker)
        yield from self._watch_filesystem_stream(turn, worker)
        try:
            observation = self._native_session.observe(
                terminal_pane=terminal_pane,
                lines=200,
                turn_marker=turn.expected_marker,
            )
        except Exception as exc:
            yield RuntimeEvent(
                type="runtime.observe_failed",
                turn_id=turn.turn_id,
                worker_id=turn.worker_id,
                payload={"source": "tmux", "error": str(exc)},
            )
            return
        text = _non_empty_str(observation.get("snapshot"))
        transcript_artifact = _non_empty_str(observation.get("transcript_artifact"))
        artifact_refs = [transcript_artifact] if transcript_artifact else []
        if text:
            yield RuntimeEvent(
                type="output.chunk",
                turn_id=turn.turn_id,
                worker_id=turn.worker_id,
                payload={"text": text},
                artifact_refs=artifact_refs,
            )
        if observation.get("marker_seen") is True:
            marker = _non_empty_str(observation.get("marker")) or turn.expected_marker
            yield RuntimeEvent(
                type="marker.detected",
                turn_id=turn.turn_id,
                worker_id=turn.worker_id,
                payload={
                    "marker": marker,
                    "source": "tmux",
                },
                artifact_refs=artifact_refs,
            )

    def _watch_filesystem_stream(self, turn: TurnEnvelope, worker: WorkerSpec | None):
        outbox_path = _required_outbox_path(turn)
        transcript_path = _transcript_path(worker)
        if outbox_path is None and transcript_path is None:
            return
        stream = FilesystemRuntimeEventStream(
            state_path=_filesystem_stream_state_path(
                outbox_path=outbox_path,
                transcript_path=transcript_path,
            )
        )
        yield from stream.poll_once(
            crew_id=turn.crew_id,
            turn_id=turn.turn_id,
            worker_id=turn.worker_id,
            outbox_path=outbox_path,
            transcript_path=transcript_path,
            expected_marker=turn.expected_marker,
            outbox_artifact_ref=_required_outbox_artifact_ref(turn) if outbox_path else None,
            transcript_artifact_ref=(
                _transcript_artifact_ref(worker, transcript_path)
                if transcript_path is not None
                else None
            ),
            autocommit=False,
        )

    def _initialize_filesystem_stream(self, turn: TurnEnvelope, worker: WorkerSpec | None) -> None:
        transcript_path = _transcript_path(worker)
        if transcript_path is None:
            return
        stream = FilesystemRuntimeEventStream(
            state_path=_filesystem_stream_state_path(
                outbox_path=_required_outbox_path(turn),
                transcript_path=transcript_path,
            )
        )
        stream.initialize_turn(turn_id=turn.turn_id, transcript_path=transcript_path)

    def commit_runtime_events(self, turn: TurnEnvelope, events: list[RuntimeEvent]) -> None:
        worker = self._workers.get(turn.worker_id)
        outbox_path = _required_outbox_path(turn)
        transcript_path = _transcript_path(worker)
        if outbox_path is None and transcript_path is None:
            return
        FilesystemRuntimeEventStream(
            state_path=_filesystem_stream_state_path(
                outbox_path=outbox_path,
                transcript_path=transcript_path,
            )
        ).commit_events(events)

    def collect_artifacts(self, turn: TurnEnvelope) -> list[str]:
        worker = self._workers.get(turn.worker_id)
        return [worker.transcript_artifact] if worker and worker.transcript_artifact else []

    def cancel_turn(self, turn: TurnEnvelope) -> CancellationResult:
        return CancellationResult(
            cancelled=False,
            reason="tmux Claude turn cancellation is not supported by this adapter",
        )

    def stop_worker(self, worker_id: str) -> StopResult:
        return StopResult(
            stopped=False,
            reason="worker stop is delegated to existing worker pool",
        )


def _compiled_turn_message(turn: TurnEnvelope) -> str:
    sections = [turn.message]
    sections.append(
        "\n".join(
            [
                "Required outbox identity:",
                f"- crew_id: {turn.crew_id}",
                f"- worker_id: {turn.worker_id}",
                f"- turn_id: {turn.turn_id}",
                f"- round_id: {turn.round_id}",
                f"- contract_id: {turn.contract_id}",
                f"- completion_mode: {turn.completion_mode}",
            ]
        )
    )
    if turn.unread_inbox_digest:
        sections.append(
            "\n".join(
                [
                    "Unread inbox:",
                    turn.unread_inbox_digest,
                    (
                        "Acknowledge message ids in outbox: "
                        f"{', '.join(turn.unread_message_ids) or 'none'}"
                    ),
                ]
            )
        )
    if turn.open_protocol_requests or turn.open_protocol_requests_digest:
        sections.append(
            "\n".join(
                [
                    "Open protocol requests:",
                    turn.open_protocol_requests_digest
                    or json.dumps(turn.open_protocol_requests, ensure_ascii=False),
                ]
            )
        )
    if turn.requires_structured_result:
        result_lines = [
            "Structured result requirement:",
            "Write a valid outbox result for this exact turn before considering the turn complete.",
        ]
        if turn.required_outbox_path:
            result_lines.extend(
                [
                    f"Required outbox file: {turn.required_outbox_path}",
                    "Create the parent directory if it does not exist.",
                    (
                        "Only this exact outbox file is watched as structured "
                        "completion evidence."
                    ),
                ]
            )
        result_lines.append(
            (
                "The outbox JSON must include crew_id, worker_id, turn_id, "
                "status, summary, changed_files, verification, "
                "acknowledged_message_ids, messages, risks, and "
                "next_suggested_action."
            )
        )
        sections.append("\n".join(result_lines))
    return "\n\n".join(section for section in sections if section)


def _required_outbox_artifact_ref(turn: TurnEnvelope) -> str:
    return f"workers/{turn.worker_id}/outbox/{turn.turn_id}.json"


def _required_outbox_path(turn: TurnEnvelope) -> Path | None:
    required_outbox_path = _non_empty_str(turn.required_outbox_path)
    return Path(required_outbox_path) if required_outbox_path else None


def _transcript_path(worker: WorkerSpec | None) -> Path | None:
    transcript_artifact = _non_empty_str(worker.transcript_artifact if worker else "")
    if not transcript_artifact:
        return None
    return Path(transcript_artifact)


def _filesystem_stream_state_path(
    *,
    outbox_path: Path | None,
    transcript_path: Path | None,
) -> Path:
    if outbox_path is not None:
        return outbox_path.with_name(f".{outbox_path.name}.v4-stream-state.json")
    if transcript_path is not None:
        return transcript_path.with_name(f".{transcript_path.name}.v4-stream-state.json")
    raise ValueError("outbox_path or transcript_path is required")


def _transcript_artifact_ref(worker: WorkerSpec | None, transcript_path: Path) -> str:
    transcript_artifact = _non_empty_str(worker.transcript_artifact if worker else "")
    if transcript_artifact and not Path(transcript_artifact).is_absolute():
        return transcript_artifact
    parts = transcript_path.parts
    for index in range(len(parts) - 4):
        if (
            parts[index] == ".orchestrator"
            and parts[index + 1] == "crews"
            and worker is not None
            and parts[index + 2] == worker.crew_id
            and parts[index + 3] == "artifacts"
        ):
            return Path(*parts[index + 4 :]).as_posix()
    return transcript_path.name
