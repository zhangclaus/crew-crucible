from __future__ import annotations

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
        result = self._native_session.send(
            terminal_pane=terminal_pane,
            message=turn.message,
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
        observation = self._native_session.observe(
            terminal_pane=terminal_pane,
            lines=200,
            turn_marker=turn.expected_marker,
        )
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
