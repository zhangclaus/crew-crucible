from __future__ import annotations

from codex_claude_orchestrator.core.models import (
    EvaluationOutcome,
    FailureClass,
    NextAction,
    PolicyDecision,
    WorkerResult,
)


class ResultEvaluator:
    def evaluate(
        self,
        result: WorkerResult,
        policy_decision: PolicyDecision | None = None,
    ) -> EvaluationOutcome:
        if policy_decision is not None and not policy_decision.allowed:
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.ASK_HUMAN,
                summary=policy_decision.reason or "policy blocked follow-up action",
                failure_class=FailureClass.POLICY_BLOCK,
                needs_human=True,
            )

        if result.parse_error:
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.RETRY_WITH_TIGHTER_PROMPT,
                summary=f"worker returned unparsable output: {result.parse_error}",
                failure_class=FailureClass.INVOCATION_ERROR,
            )

        if result.exit_code != 0:
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.RETRY_SAME_AGENT,
                summary="worker process exited with a non-zero status",
                failure_class=FailureClass.EXECUTION_ERROR,
            )

        if result.structured_output is None:
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.RETRY_WITH_TIGHTER_PROMPT,
                summary="worker returned no structured payload",
                failure_class=FailureClass.QUALITY_REJECT,
            )

        summary = str(result.structured_output.get("summary", "")).strip()
        status = result.structured_output.get("status")

        if not summary:
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.RETRY_WITH_TIGHTER_PROMPT,
                summary="worker payload is missing a summary",
                failure_class=FailureClass.QUALITY_REJECT,
            )

        if status == "needs_human":
            return EvaluationOutcome(
                accepted=False,
                next_action=NextAction.ASK_HUMAN,
                summary=summary,
                needs_human=True,
            )

        return EvaluationOutcome(
            accepted=True,
            next_action=NextAction.ACCEPT,
            summary=summary,
        )
