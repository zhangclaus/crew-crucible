from codex_claude_orchestrator.crew.gates import GateResult
from codex_claude_orchestrator.crew.readiness import CrewReadinessEvaluator
from codex_claude_orchestrator.crew.review_verdict import ReviewVerdict


def test_readiness_evaluator_marks_ready_with_review_ok_and_verification_passed():
    report = CrewReadinessEvaluator().evaluate(
        round_id="round-1",
        worker_id="worker-source",
        contract_id="contract-source",
        changed_files=["src/app.py"],
        scope_result=GateResult(status="pass", reason="inside scope", evidence_refs=["changes.json"]),
        review_verdict=ReviewVerdict(status="ok", summary="safe", evidence_refs=["review.json"]),
        verification_results=[{"passed": True, "summary": "command passed", "stdout_artifact": "stdout.txt"}],
    )

    assert report.status == "ready"
    assert report.scope_status == "pass"
    assert report.review_status == "ok"
    assert report.verification_status == "pass"
    assert report.warnings == []
    assert report.blockers == []
    assert "changes.json" in report.evidence_refs
    assert "review.json" in report.evidence_refs
    assert "stdout.txt" in report.evidence_refs


def test_readiness_evaluator_preserves_review_warning():
    report = CrewReadinessEvaluator().evaluate(
        round_id="round-1",
        worker_id="worker-source",
        contract_id="contract-source",
        changed_files=["src/app.py"],
        scope_result=GateResult(status="pass", reason="inside scope"),
        review_verdict=ReviewVerdict(status="warn", summary="minor risk", findings=["risk remains"]),
        verification_results=[{"passed": True, "summary": "command passed"}],
    )

    assert report.status == "ready"
    assert report.review_status == "warn"
    assert report.warnings == ["review warning: minor risk", "risk remains"]


def test_readiness_evaluator_blocks_unknown_review():
    report = CrewReadinessEvaluator().evaluate(
        round_id="round-1",
        worker_id="worker-source",
        contract_id="contract-source",
        changed_files=["src/app.py"],
        scope_result=GateResult(status="pass", reason="inside scope"),
        review_verdict=ReviewVerdict(status="unknown", summary="review verdict was not parseable"),
        verification_results=[],
    )

    assert report.status == "blocked"
    assert report.review_status == "unknown"
    assert "review verdict was not parseable" in report.blockers


def test_readiness_evaluator_challenges_review_block():
    report = CrewReadinessEvaluator().evaluate(
        round_id="round-1",
        worker_id="worker-source",
        contract_id="contract-source",
        changed_files=["src/app.py"],
        scope_result=GateResult(status="pass", reason="inside scope"),
        review_verdict=ReviewVerdict(status="block", summary="regression", findings=["retry broke"]),
        verification_results=[],
    )

    assert report.status == "challenge"
    assert report.review_status == "block"
    assert report.blockers == ["review blocked: regression", "retry broke"]


def test_readiness_evaluator_challenges_failed_verification():
    report = CrewReadinessEvaluator().evaluate(
        round_id="round-1",
        worker_id="worker-source",
        contract_id="contract-source",
        changed_files=[],
        scope_result=GateResult(status="pass", reason="no changed files"),
        review_verdict=None,
        verification_results=[{"passed": False, "summary": "command failed", "stderr_artifact": "stderr.txt"}],
    )

    assert report.status == "challenge"
    assert report.review_status == "skipped"
    assert report.verification_status == "fail"
    assert report.blockers == ["command failed"]
    assert "stderr.txt" in report.evidence_refs
