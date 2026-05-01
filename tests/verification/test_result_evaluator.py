from codex_claude_orchestrator.core.models import FailureClass, NextAction, WorkerResult
from codex_claude_orchestrator.verification.result_evaluator import ResultEvaluator


def test_evaluator_distinguishes_parse_errors_execution_failures_and_success():
    evaluator = ResultEvaluator()

    parse_outcome = evaluator.evaluate(
        WorkerResult(
            raw_output="not-json",
            stdout="not-json",
            stderr="",
            exit_code=0,
            parse_error="invalid json",
        )
    )
    execution_outcome = evaluator.evaluate(
        WorkerResult(
            raw_output="",
            stdout="",
            stderr="command failed",
            exit_code=2,
        )
    )
    success_outcome = evaluator.evaluate(
        WorkerResult(
            raw_output='{"summary":"done","status":"completed"}',
            stdout='{"summary":"done","status":"completed"}',
            stderr="",
            exit_code=0,
            structured_output={
                "summary": "done",
                "status": "completed",
                "changed_files": ["app.py"],
                "verification_commands": ["pytest tests/test_result_evaluator.py -v"],
                "notes_for_supervisor": [],
            },
            changed_files=["app.py"],
        )
    )

    assert parse_outcome.failure_class is FailureClass.INVOCATION_ERROR
    assert parse_outcome.next_action is NextAction.RETRY_WITH_TIGHTER_PROMPT
    assert execution_outcome.failure_class is FailureClass.EXECUTION_ERROR
    assert execution_outcome.next_action is NextAction.RETRY_SAME_AGENT
    assert success_outcome.accepted is True
