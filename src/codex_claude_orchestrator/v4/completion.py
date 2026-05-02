"""Completion detection for V4 runtime turns."""

from __future__ import annotations

from dataclasses import dataclass, field

from codex_claude_orchestrator.v4.runtime import RuntimeEvent, TurnEnvelope


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    event_type: str
    reason: str
    evidence_refs: list[str] = field(default_factory=list)


class CompletionDetector:
    @staticmethod
    def evaluate(
        turn: TurnEnvelope,
        events: list[RuntimeEvent],
        contract_marker: str = "",
        timed_out: bool = False,
    ) -> CompletionDecision:
        output_text = "".join(
            str(event.payload.get("text", ""))
            for event in events
            if event.type in {"output.chunk", "runtime.output.appended"}
        )
        evidence_refs = list(
            dict.fromkeys(
                artifact_ref
                for event in events
                for artifact_ref in event.artifact_refs
            )
        )

        marker_detected = any(
            event.type == "marker.detected"
            and event.payload.get("marker") == turn.expected_marker
            for event in events
        )

        outbox_detected = any(
            event.type == "worker.outbox.detected"
            and event.payload.get("valid") is True
            for event in events
        )
        if outbox_detected:
            return CompletionDecision(
                event_type="turn.completed",
                reason="valid outbox result detected",
                evidence_refs=evidence_refs,
            )

        if turn.expected_marker and (turn.expected_marker in output_text or marker_detected):
            if turn.requires_structured_result and turn.completion_mode != "marker_allowed":
                return CompletionDecision(
                    event_type="turn.inconclusive",
                    reason="missing_outbox",
                    evidence_refs=evidence_refs,
                )
            return CompletionDecision(
                event_type="turn.completed",
                reason="expected marker detected",
                evidence_refs=evidence_refs,
            )

        if contract_marker and contract_marker in output_text:
            return CompletionDecision(
                event_type="turn.inconclusive",
                reason="contract marker found but expected turn marker was missing",
                evidence_refs=evidence_refs,
            )

        for event in events:
            if event.type in {"process.exited", "runtime.process_exited"}:
                return CompletionDecision(
                    event_type="turn.failed",
                    reason=event.payload.get("reason")
                    or "process exited before completion",
                    evidence_refs=evidence_refs,
                )

        deadline_reached = any(event.type == "turn.deadline_reached" for event in events)
        if timed_out or deadline_reached:
            return CompletionDecision(
                event_type="turn.timeout",
                reason="deadline reached before completion evidence",
                evidence_refs=evidence_refs,
            )

        return CompletionDecision(
            event_type="turn.inconclusive",
            reason="completion evidence not found",
            evidence_refs=evidence_refs,
        )
