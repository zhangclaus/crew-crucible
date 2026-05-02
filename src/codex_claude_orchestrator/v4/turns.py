"""Turn delivery service for the durable V4 runtime."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock

from codex_claude_orchestrator.v4.event_store import SQLiteEventStore
from codex_claude_orchestrator.v4.runtime import DeliveryResult, RuntimeAdapter, TurnEnvelope


_delivery_locks_guard = Lock()
_delivery_locks: defaultdict[tuple[str, str, int], Lock] = defaultdict(Lock)


class TurnService:
    def __init__(self, *, event_store: SQLiteEventStore, adapter: RuntimeAdapter) -> None:
        self._events = event_store
        self._adapter = adapter

    def request_and_deliver(self, turn: TurnEnvelope) -> DeliveryResult:
        delivered_result = self._stored_delivered_result(turn)
        if delivered_result is not None:
            return delivered_result

        failed_result = self._stored_failed_result(turn)
        if failed_result is not None:
            return failed_result

        lock_key = (turn.crew_id, turn.turn_id, turn.attempt)
        with _delivery_locks_guard:
            delivery_lock = _delivery_locks[lock_key]

        with delivery_lock:
            delivered_result = self._stored_delivered_result(turn)
            if delivered_result is not None:
                return delivered_result

            failed_result = self._stored_failed_result(turn)
            if failed_result is not None:
                return failed_result

            return self._request_and_deliver_claimed(turn)

    def _request_and_deliver_claimed(self, turn: TurnEnvelope) -> DeliveryResult:
        self._events.append(
            stream_id=turn.crew_id,
            type="turn.requested",
            crew_id=turn.crew_id,
            worker_id=turn.worker_id,
            turn_id=turn.turn_id,
            round_id=turn.round_id,
            contract_id=turn.contract_id,
            idempotency_key=f"{turn.idempotency_key}/attempt-{turn.attempt}/requested",
            payload={
                "round_id": turn.round_id,
                "phase": turn.phase,
                "message": turn.message,
                "expected_marker": turn.expected_marker,
                "deadline_at": turn.deadline_at,
                "attempt": turn.attempt,
            },
        )

        claim_event, inserted = self._events.append_claim(
            stream_id=turn.crew_id,
            type="turn.delivery_started",
            crew_id=turn.crew_id,
            worker_id=turn.worker_id,
            turn_id=turn.turn_id,
            round_id=turn.round_id,
            contract_id=turn.contract_id,
            idempotency_key=f"{turn.idempotency_key}/attempt-{turn.attempt}/delivery-started",
        )
        if not inserted:
            delivered_result = self._stored_delivered_result(turn)
            if delivered_result is not None:
                return delivered_result

            failed_result = self._stored_failed_result(turn)
            if failed_result is not None:
                return failed_result

            return DeliveryResult(
                delivered=False,
                marker=turn.expected_marker,
                reason="delivery already in progress",
                artifact_refs=list(claim_event.artifact_refs),
            )

        result = self._adapter.deliver_turn(turn)
        if result.delivered:
            self._events.append(
                stream_id=turn.crew_id,
                type="turn.delivered",
                crew_id=turn.crew_id,
                worker_id=turn.worker_id,
                turn_id=turn.turn_id,
                round_id=turn.round_id,
                contract_id=turn.contract_id,
                idempotency_key=f"{turn.idempotency_key}/delivered",
                payload={"marker": result.marker, "reason": result.reason},
                artifact_refs=result.artifact_refs,
            )
        else:
            self._events.append(
                stream_id=turn.crew_id,
                type="turn.delivery_failed",
                crew_id=turn.crew_id,
                worker_id=turn.worker_id,
                turn_id=turn.turn_id,
                round_id=turn.round_id,
                contract_id=turn.contract_id,
                idempotency_key=f"{turn.idempotency_key}/delivery-failed/{turn.attempt}",
                payload={"marker": result.marker, "reason": result.reason},
                artifact_refs=result.artifact_refs,
            )

        return result

    def _stored_delivered_result(self, turn: TurnEnvelope) -> DeliveryResult | None:
        delivered_event = self._events.get_by_idempotency_key(
            f"{turn.idempotency_key}/delivered"
        )
        if delivered_event is None:
            return None

        return DeliveryResult(
            delivered=True,
            marker=delivered_event.payload.get("marker", turn.expected_marker),
            reason="already delivered",
            artifact_refs=list(delivered_event.artifact_refs),
        )

    def _stored_failed_result(self, turn: TurnEnvelope) -> DeliveryResult | None:
        failed_event = self._events.get_by_idempotency_key(
            f"{turn.idempotency_key}/delivery-failed/{turn.attempt}"
        )
        if failed_event is None:
            return None

        return DeliveryResult(
            delivered=False,
            marker=failed_event.payload.get("marker", turn.expected_marker),
            reason=failed_event.payload.get("reason", ""),
            artifact_refs=list(failed_event.artifact_refs),
        )
