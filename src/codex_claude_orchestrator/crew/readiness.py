from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.crew.gates import GateResult
from codex_claude_orchestrator.crew.review_verdict import ReviewVerdict


def _normalize(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {item.name: _normalize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {_normalize(key): _normalize(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_normalize(inner) for inner in value]
    return value


@dataclass(slots=True)
class ReadinessReport:
    round_id: str
    worker_id: str
    contract_id: str
    status: str
    scope_status: str
    review_status: str
    verification_status: str
    changed_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


class CrewReadinessEvaluator:
    def evaluate(
        self,
        *,
        round_id: str,
        worker_id: str,
        contract_id: str,
        changed_files: list[str | Path],
        scope_result: GateResult,
        review_verdict: ReviewVerdict | None,
        verification_results: list[dict[str, Any]],
    ) -> ReadinessReport:
        evidence_refs: list[str] = []
        warnings: list[str] = []
        blockers: list[str] = []

        self._extend_refs(evidence_refs, scope_result.evidence_refs)
        scope_status = scope_result.status
        if scope_status == "challenge":
            blockers.append(f"scope challenge: {scope_result.reason}")
        elif scope_status != "pass":
            blockers.append(scope_result.reason)

        review_status = "skipped"
        if review_verdict is not None:
            review_status = review_verdict.status
            self._extend_refs(evidence_refs, review_verdict.evidence_refs)
            if review_status == "warn":
                warnings.append(f"review warning: {review_verdict.summary}")
                warnings.extend(review_verdict.findings)
            elif review_status == "block":
                blockers.append(f"review blocked: {review_verdict.summary}")
                blockers.extend(review_verdict.findings)
            elif review_status == "unknown":
                blockers.append(review_verdict.summary)

        verification_status = self._verification_status(verification_results)
        for result in verification_results:
            self._append_artifact_ref(evidence_refs, result, "stdout_artifact")
            self._append_artifact_ref(evidence_refs, result, "stderr_artifact")
            if result.get("passed") is False:
                blockers.append(str(result.get("summary", "verification failed")))

        status = self._readiness_status(
            scope_status=scope_status,
            review_status=review_status,
            verification_status=verification_status,
            blockers=blockers,
        )

        return ReadinessReport(
            round_id=round_id,
            worker_id=worker_id,
            contract_id=contract_id,
            status=status,
            scope_status=scope_status,
            review_status=review_status,
            verification_status=verification_status,
            changed_files=[str(path) for path in changed_files],
            warnings=warnings,
            blockers=blockers,
            evidence_refs=evidence_refs,
        )

    def _verification_status(self, verification_results: list[dict[str, Any]]) -> str:
        if not verification_results:
            return "skipped"
        if any(result.get("passed") is False for result in verification_results):
            return "fail"
        return "pass"

    def _readiness_status(
        self,
        *,
        scope_status: str,
        review_status: str,
        verification_status: str,
        blockers: list[str],
    ) -> str:
        if review_status == "unknown" or scope_status == "block":
            return "blocked"
        if review_status == "block" or scope_status == "challenge" or verification_status == "fail":
            return "challenge"
        if verification_status == "skipped":
            if blockers:
                return "blocked"
            blockers.append("verification skipped")
            return "blocked"
        return "ready"

    def _extend_refs(self, evidence_refs: list[str], refs: list[str]) -> None:
        for ref in refs:
            if ref not in evidence_refs:
                evidence_refs.append(ref)

    def _append_artifact_ref(
        self,
        evidence_refs: list[str],
        result: dict[str, Any],
        artifact_key: str,
    ) -> None:
        artifact = result.get(artifact_key)
        if artifact and artifact not in evidence_refs:
            evidence_refs.append(str(artifact))
