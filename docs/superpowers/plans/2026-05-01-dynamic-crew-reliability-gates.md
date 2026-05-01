# Dynamic Crew Reliability Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dynamic crew acceptance depend on hard scope, review, verification, readiness, and marker evidence gates.

**Architecture:** Add small gate/parser/evaluator modules, then thread them through `CrewSupervisorLoop.supervise_dynamic()` without rewriting the whole supervisor. `CrewController` gets narrow artifact and blackboard recording helpers so supervisor gate results are persisted through existing state infrastructure.

**Tech Stack:** Python 3.11 dataclasses, existing JSON artifact recorder, pytest, existing crew/controller/supervisor test fakes.

---

## File Structure

- Create `src/codex_claude_orchestrator/crew/review_verdict.py`
  - Owns `ReviewVerdict` and `ReviewVerdictParser`.
  - Parses `<<<CODEX_REVIEW` blocks and plain `Verdict: BLOCK` text.

- Create `src/codex_claude_orchestrator/crew/gates.py`
  - Owns `GateResult` and `WriteScopeGate`.
  - Evaluates changed files against `WorkerContract.write_scope` after diff recording.

- Create `src/codex_claude_orchestrator/crew/readiness.py`
  - Owns `ReadinessReport` and `CrewReadinessEvaluator`.
  - Collapses scope, review, browser, and verification evidence into one JSON-ready report.

- Create `src/codex_claude_orchestrator/runtime/marker_policy.py`
  - Owns `MarkerObservation` and `MarkerObservationPolicy`.
  - Adds transcript fallback and marker mismatch reasons.

- Modify `src/codex_claude_orchestrator/crew/controller.py`
  - Add `write_json_artifact()` and `record_blackboard_entry()`.
  - Keep state writes centralized in `CrewRecorder` and `BlackboardStore`.

- Modify `src/codex_claude_orchestrator/crew/supervisor_loop.py`
  - Instantiate the new gate components.
  - Insert write-scope gate after `changes()`.
  - Parse review verdict after patch auditor observation.
  - Use readiness evaluator before returning `ready_for_codex_accept`.
  - Use marker policy inside `_wait_for_marker()`.

- Modify `tests/crew/test_supervisor_loop.py`
  - Extend `FakeController` to record artifacts and return parseable review output.
  - Add dynamic gate behavior tests.

- Create `tests/crew/test_review_verdict.py`
- Create `tests/crew/test_gates.py`
- Create `tests/crew/test_readiness.py`
- Create `tests/runtime/test_marker_policy.py`

## Task 1: Review Verdict Parser

**Files:**
- Create: `src/codex_claude_orchestrator/crew/review_verdict.py`
- Test: `tests/crew/test_review_verdict.py`

- [ ] **Step 1: Write failing parser tests**

Create `tests/crew/test_review_verdict.py` with:

```python
from codex_claude_orchestrator.crew.review_verdict import ReviewVerdictParser


def test_review_verdict_parser_parses_structured_ok_block():
    text = """review notes
<<<CODEX_REVIEW
verdict: OK
summary: Patch is safe.
findings:
- Tests cover the changed path.
>>>
done"""

    verdict = ReviewVerdictParser().parse(
        text,
        evidence_refs=["workers/worker-reviewer/transcript.txt"],
        raw_artifact="workers/worker-reviewer/transcript.txt",
    )

    assert verdict.status == "ok"
    assert verdict.summary == "Patch is safe."
    assert verdict.findings == ["Tests cover the changed path."]
    assert verdict.evidence_refs == ["workers/worker-reviewer/transcript.txt"]
    assert verdict.raw_artifact == "workers/worker-reviewer/transcript.txt"
    assert verdict.to_dict()["status"] == "ok"


def test_review_verdict_parser_parses_structured_warn_block():
    text = """<<<CODEX_REVIEW
verdict: WARN
summary: Patch is acceptable with a follow-up risk.
findings:
- The behavior is covered, but the fixture name is broad.
>>>"""

    verdict = ReviewVerdictParser().parse(text)

    assert verdict.status == "warn"
    assert verdict.summary == "Patch is acceptable with a follow-up risk."
    assert verdict.findings == ["The behavior is covered, but the fixture name is broad."]


def test_review_verdict_parser_parses_structured_block_block():
    text = """<<<CODEX_REVIEW
verdict: BLOCK
summary: Patch regresses retry behavior.
findings:
- The retry counter is reset inside the loop.
- The new test does not exercise the failure path.
>>>"""

    verdict = ReviewVerdictParser().parse(text)

    assert verdict.status == "block"
    assert verdict.summary == "Patch regresses retry behavior."
    assert verdict.findings == [
        "The retry counter is reset inside the loop.",
        "The new test does not exercise the failure path.",
    ]


def test_review_verdict_parser_parses_plain_text_fallback():
    text = """Reviewer output
Verdict: BLOCK
Summary: Missing assertion for failed verification.
Findings:
- Verification failure is swallowed.
"""

    verdict = ReviewVerdictParser().parse(text)

    assert verdict.status == "block"
    assert verdict.summary == "Missing assertion for failed verification."
    assert verdict.findings == ["Verification failure is swallowed."]


def test_review_verdict_parser_returns_unknown_for_unparseable_output():
    verdict = ReviewVerdictParser().parse("Looks fine to me without a verdict line.")

    assert verdict.status == "unknown"
    assert verdict.summary == "review verdict was not parseable"
    assert verdict.findings == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_review_verdict.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.crew.review_verdict'`.

- [ ] **Step 3: Implement parser**

Create `src/codex_claude_orchestrator/crew/review_verdict.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {item.name: _normalize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {key: _normalize(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_normalize(inner) for inner in value]
    return value


@dataclass(frozen=True, slots=True)
class ReviewVerdict:
    status: str
    summary: str
    findings: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    raw_artifact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


class ReviewVerdictParser:
    _block_pattern = re.compile(r"<<<CODEX_REVIEW\s*\n(?P<body>.*?)\n?>>>", re.DOTALL | re.IGNORECASE)
    _verdict_pattern = re.compile(r"^\s*verdict\s*:\s*(OK|WARN|BLOCK)\s*$", re.IGNORECASE | re.MULTILINE)
    _summary_pattern = re.compile(r"^\s*summary\s*:\s*(?P<summary>.+?)\s*$", re.IGNORECASE | re.MULTILINE)

    def parse(
        self,
        text: str,
        *,
        evidence_refs: list[str] | None = None,
        raw_artifact: str = "",
    ) -> ReviewVerdict:
        body = self._review_body(text)
        verdict = self._parse_verdict(body)
        if verdict is None:
            return ReviewVerdict(
                status="unknown",
                summary="review verdict was not parseable",
                findings=[],
                evidence_refs=evidence_refs or [],
                raw_artifact=raw_artifact,
            )
        return ReviewVerdict(
            status={"OK": "ok", "WARN": "warn", "BLOCK": "block"}[verdict],
            summary=self._parse_summary(body) or self._default_summary(verdict),
            findings=self._parse_findings(body),
            evidence_refs=evidence_refs or [],
            raw_artifact=raw_artifact,
        )

    def _review_body(self, text: str) -> str:
        match = self._block_pattern.search(text)
        return match.group("body") if match else text

    def _parse_verdict(self, body: str) -> str | None:
        match = self._verdict_pattern.search(body)
        return match.group(1).upper() if match else None

    def _parse_summary(self, body: str) -> str:
        match = self._summary_pattern.search(body)
        return match.group("summary").strip() if match else ""

    def _parse_findings(self, body: str) -> list[str]:
        lines = body.splitlines()
        findings: list[str] = []
        in_findings = False
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                if in_findings:
                    continue
                continue
            if re.match(r"^findings\s*:\s*$", line, re.IGNORECASE):
                in_findings = True
                continue
            if in_findings and re.match(r"^[A-Za-z_ -]+\s*:", line):
                break
            if in_findings:
                cleaned = line[1:].strip() if line.startswith("-") else line
                if cleaned:
                    findings.append(cleaned)
        return findings

    def _default_summary(self, verdict: str) -> str:
        return {
            "OK": "review passed",
            "WARN": "review passed with warnings",
            "BLOCK": "review blocked the patch",
        }[verdict]
```

- [ ] **Step 4: Run parser tests**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_review_verdict.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Commit parser task**

```bash
git add src/codex_claude_orchestrator/crew/review_verdict.py tests/crew/test_review_verdict.py
git commit -m "feat: add crew review verdict parser"
```

## Task 2: Write Scope Gate

**Files:**
- Create: `src/codex_claude_orchestrator/crew/gates.py`
- Test: `tests/crew/test_gates.py`

- [ ] **Step 1: Write failing scope gate tests**

Create `tests/crew/test_gates.py` with:

```python
from codex_claude_orchestrator.crew.gates import WriteScopeGate


def test_write_scope_gate_passes_when_changed_files_are_in_scope():
    result = WriteScopeGate().evaluate(
        changed_files=["src/app.py", "tests/test_app.py"],
        write_scope=["src/", "tests/"],
        evidence_refs=["workers/worker-source/changes.json"],
    )

    assert result.status == "pass"
    assert result.reason == "all changed files are inside write_scope"
    assert result.details["out_of_scope"] == []
    assert result.evidence_refs == ["workers/worker-source/changes.json"]


def test_write_scope_gate_passes_when_no_files_changed():
    result = WriteScopeGate().evaluate(changed_files=[], write_scope=[])

    assert result.status == "pass"
    assert result.reason == "no changed files"


def test_write_scope_gate_challenges_low_risk_out_of_scope_file():
    result = WriteScopeGate().evaluate(
        changed_files=["docs/notes.md"],
        write_scope=["src/", "tests/"],
    )

    assert result.status == "challenge"
    assert result.details["out_of_scope"] == ["docs/notes.md"]
    assert result.details["protected"] == []
    assert "outside write_scope" in result.reason


def test_write_scope_gate_blocks_protected_out_of_scope_file():
    result = WriteScopeGate().evaluate(
        changed_files=[".github/workflows/ci.yml"],
        write_scope=["src/", "tests/"],
    )

    assert result.status == "block"
    assert result.details["protected"] == [".github/workflows/ci.yml"]
    assert "protected" in result.reason


def test_write_scope_gate_blocks_changes_when_scope_is_empty():
    result = WriteScopeGate().evaluate(changed_files=["src/app.py"], write_scope=[])

    assert result.status == "block"
    assert result.reason == "write_scope is empty but files changed"
    assert result.details["out_of_scope"] == ["src/app.py"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_gates.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.crew.gates'`.

- [ ] **Step 3: Implement write scope gate**

Create `src/codex_claude_orchestrator/crew/gates.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {item.name: _normalize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {key: _normalize(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_normalize(inner) for inner in value]
    return value


@dataclass(frozen=True, slots=True)
class GateResult:
    status: str
    reason: str
    evidence_refs: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


class WriteScopeGate:
    def __init__(self, protected_patterns: list[str] | None = None):
        self._protected_patterns = protected_patterns or [
            ".git/",
            ".env",
            "secrets/",
            "*.pem",
            "*.key",
            "pyproject.toml",
            "package-lock.json",
            "pnpm-lock.yaml",
            "uv.lock",
            ".github/workflows/",
        ]

    def evaluate(
        self,
        *,
        changed_files: list[str],
        write_scope: list[str],
        evidence_refs: list[str] | None = None,
    ) -> GateResult:
        normalized_changed = [self._normalize_path(path) for path in changed_files if path]
        normalized_scope = [self._normalize_scope(scope) for scope in write_scope if scope]
        evidence = evidence_refs or []

        if not normalized_changed:
            return GateResult(
                status="pass",
                reason="no changed files",
                evidence_refs=evidence,
                details={"changed_files": [], "write_scope": normalized_scope, "out_of_scope": [], "protected": []},
            )

        if not normalized_scope:
            return GateResult(
                status="block",
                reason="write_scope is empty but files changed",
                evidence_refs=evidence,
                details={
                    "changed_files": normalized_changed,
                    "write_scope": [],
                    "out_of_scope": normalized_changed,
                    "protected": self._protected_paths(normalized_changed),
                },
            )

        out_of_scope = [path for path in normalized_changed if not self._is_in_scope(path, normalized_scope)]
        protected = self._protected_paths(out_of_scope)
        if protected:
            return GateResult(
                status="block",
                reason=f"protected out-of-scope paths changed: {', '.join(protected)}",
                evidence_refs=evidence,
                details={
                    "changed_files": normalized_changed,
                    "write_scope": normalized_scope,
                    "out_of_scope": out_of_scope,
                    "protected": protected,
                },
            )
        if out_of_scope:
            return GateResult(
                status="challenge",
                reason=f"changed files outside write_scope: {', '.join(out_of_scope)}",
                evidence_refs=evidence,
                details={
                    "changed_files": normalized_changed,
                    "write_scope": normalized_scope,
                    "out_of_scope": out_of_scope,
                    "protected": [],
                },
            )
        return GateResult(
            status="pass",
            reason="all changed files are inside write_scope",
            evidence_refs=evidence,
            details={
                "changed_files": normalized_changed,
                "write_scope": normalized_scope,
                "out_of_scope": [],
                "protected": [],
            },
        )

    def _normalize_path(self, path: str) -> str:
        normalized = path.replace("\\", "/")
        return normalized[2:] if normalized.startswith("./") else normalized

    def _normalize_scope(self, scope: str) -> str:
        normalized = self._normalize_path(scope)
        return normalized if normalized.endswith("/") else normalized

    def _is_in_scope(self, path: str, scopes: list[str]) -> bool:
        for scope in scopes:
            if scope.endswith("/") and path.startswith(scope):
                return True
            if path == scope or path.startswith(f"{scope}/"):
                return True
        return False

    def _protected_paths(self, paths: list[str]) -> list[str]:
        return [path for path in paths if self._is_protected(path)]

    def _is_protected(self, path: str) -> bool:
        for pattern in self._protected_patterns:
            if pattern.endswith("/") and path.startswith(pattern):
                return True
            if fnmatch(path, pattern):
                return True
        return False
```

- [ ] **Step 4: Run scope gate tests**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_gates.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Commit scope gate task**

```bash
git add src/codex_claude_orchestrator/crew/gates.py tests/crew/test_gates.py
git commit -m "feat: add crew write scope gate"
```

## Task 3: Readiness Evaluator

**Files:**
- Create: `src/codex_claude_orchestrator/crew/readiness.py`
- Test: `tests/crew/test_readiness.py`

- [ ] **Step 1: Write failing readiness tests**

Create `tests/crew/test_readiness.py` with:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_readiness.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.crew.readiness'`.

- [ ] **Step 3: Implement readiness evaluator**

Create `src/codex_claude_orchestrator/crew/readiness.py`:

```python
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
        return {key: _normalize(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_normalize(inner) for inner in value]
    return value


@dataclass(frozen=True, slots=True)
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
        changed_files: list[str],
        scope_result: GateResult,
        review_verdict: ReviewVerdict | None,
        verification_results: list[dict[str, Any]],
    ) -> ReadinessReport:
        warnings: list[str] = []
        blockers: list[str] = []
        evidence_refs: list[str] = []
        evidence_refs.extend(scope_result.evidence_refs)

        review_status = "skipped" if review_verdict is None else review_verdict.status
        if review_verdict is not None:
            evidence_refs.extend(review_verdict.evidence_refs)
            if review_verdict.status == "warn":
                warnings.append(f"review warning: {review_verdict.summary}")
                warnings.extend(review_verdict.findings)
            if review_verdict.status == "block":
                blockers.append(f"review blocked: {review_verdict.summary}")
                blockers.extend(review_verdict.findings)
            if review_verdict.status == "unknown":
                blockers.append(review_verdict.summary)

        for result in verification_results:
            for key in ("stdout_artifact", "stderr_artifact"):
                artifact = result.get(key)
                if artifact:
                    evidence_refs.append(str(artifact))

        verification_status = self._verification_status(verification_results)
        if scope_result.status == "block":
            blockers.append(scope_result.reason)
        if scope_result.status == "challenge":
            blockers.append(scope_result.reason)
        if verification_status == "fail":
            blockers.extend(result.get("summary", "verification failed") for result in verification_results if not result.get("passed", False))
        if verification_status == "skipped":
            blockers.append("verification did not run")

        status = self._overall_status(scope_result.status, review_status, verification_status, blockers)
        return ReadinessReport(
            round_id=round_id,
            worker_id=worker_id,
            contract_id=contract_id,
            status=status,
            scope_status=scope_result.status,
            review_status=review_status,
            verification_status=verification_status,
            changed_files=list(changed_files),
            warnings=warnings,
            blockers=blockers,
            evidence_refs=list(dict.fromkeys(evidence_refs)),
        )

    def _verification_status(self, verification_results: list[dict[str, Any]]) -> str:
        if not verification_results:
            return "skipped"
        if all(result.get("passed", False) for result in verification_results):
            return "pass"
        return "fail"

    def _overall_status(
        self,
        scope_status: str,
        review_status: str,
        verification_status: str,
        blockers: list[str],
    ) -> str:
        if scope_status == "block" or review_status == "unknown":
            return "blocked"
        if scope_status == "challenge" or review_status == "block" or verification_status == "fail":
            return "challenge"
        if blockers:
            return "blocked"
        return "ready"
```

- [ ] **Step 4: Run readiness tests**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_readiness.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Commit readiness task**

```bash
git add src/codex_claude_orchestrator/crew/readiness.py tests/crew/test_readiness.py
git commit -m "feat: add crew readiness evaluator"
```

## Task 4: Marker Observation Policy

**Files:**
- Create: `src/codex_claude_orchestrator/runtime/marker_policy.py`
- Test: `tests/runtime/test_marker_policy.py`

- [ ] **Step 1: Write failing marker policy tests**

Create `tests/runtime/test_marker_policy.py` with:

```python
from codex_claude_orchestrator.runtime.marker_policy import MarkerObservationPolicy


def test_marker_policy_completes_when_pane_snapshot_contains_exact_marker():
    observation = MarkerObservationPolicy().evaluate(
        snapshot="worker done\n<<<CODEX_TURN_DONE crew=crew-1 worker=w phase=p round=1>>>",
        expected_marker="<<<CODEX_TURN_DONE crew=crew-1 worker=w phase=p round=1>>>",
        transcript_text="",
        transcript_artifact="workers/w/transcript.txt",
    )

    assert observation.status == "completed"
    assert observation.marker_seen is True
    assert observation.reason == "marker found in pane snapshot"
    assert observation.evidence_refs == ["workers/w/transcript.txt"]


def test_marker_policy_completes_when_transcript_contains_marker():
    observation = MarkerObservationPolicy().evaluate(
        snapshot="last pane lines without marker",
        expected_marker="<<<CODEX_TURN_DONE crew=crew-1 worker=w phase=p round=1>>>",
        transcript_text="older transcript\n<<<CODEX_TURN_DONE crew=crew-1 worker=w phase=p round=1>>>",
        transcript_artifact="workers/w/transcript.txt",
    )

    assert observation.status == "completed"
    assert observation.marker_seen is True
    assert observation.reason == "marker found in transcript"


def test_marker_policy_reports_mismatch_for_contract_marker_only():
    observation = MarkerObservationPolicy().evaluate(
        snapshot="<<<CODEX_TURN_DONE crew=crew-1 contract=source_write>>>",
        expected_marker="<<<CODEX_TURN_DONE crew=crew-1 worker=w phase=p round=1>>>",
        transcript_text="",
        contract_marker="<<<CODEX_TURN_DONE crew=crew-1 contract=source_write>>>",
    )

    assert observation.status == "mismatch"
    assert observation.marker_seen is False
    assert observation.reason == "contract marker found but expected turn marker was missing"


def test_marker_policy_waits_when_no_marker_is_present():
    observation = MarkerObservationPolicy().evaluate(
        snapshot="still working",
        expected_marker="<<<CODEX_TURN_DONE crew=crew-1 worker=w phase=p round=1>>>",
        transcript_text="still working",
    )

    assert observation.status == "waiting"
    assert observation.marker_seen is False
    assert observation.reason == "expected marker not found"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/runtime/test_marker_policy.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'codex_claude_orchestrator.runtime.marker_policy'`.

- [ ] **Step 3: Implement marker policy**

Create `src/codex_claude_orchestrator/runtime/marker_policy.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {item.name: _normalize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {key: _normalize(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_normalize(inner) for inner in value]
    return value


@dataclass(frozen=True, slots=True)
class MarkerObservation:
    status: str
    marker_seen: bool
    reason: str
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


class MarkerObservationPolicy:
    def evaluate(
        self,
        *,
        snapshot: str,
        expected_marker: str,
        transcript_text: str = "",
        transcript_artifact: str = "",
        contract_marker: str = "",
    ) -> MarkerObservation:
        evidence_refs = [transcript_artifact] if transcript_artifact else []
        if expected_marker and expected_marker in snapshot:
            return MarkerObservation("completed", True, "marker found in pane snapshot", evidence_refs)
        if expected_marker and transcript_text and expected_marker in transcript_text:
            return MarkerObservation("completed", True, "marker found in transcript", evidence_refs)
        if contract_marker and (contract_marker in snapshot or contract_marker in transcript_text):
            return MarkerObservation(
                "mismatch",
                False,
                "contract marker found but expected turn marker was missing",
                evidence_refs,
            )
        return MarkerObservation("waiting", False, "expected marker not found", evidence_refs)
```

- [ ] **Step 4: Run marker policy tests**

Run:

```bash
.venv/bin/python -m pytest tests/runtime/test_marker_policy.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit marker policy task**

```bash
git add src/codex_claude_orchestrator/runtime/marker_policy.py tests/runtime/test_marker_policy.py
git commit -m "feat: add marker observation policy"
```

## Task 5: Controller Recording Helpers

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/controller.py`
- Test: `tests/crew/test_controller.py`

- [ ] **Step 1: Write failing controller helper test**

Append to `tests/crew/test_controller.py`:

```python
def test_controller_records_gate_artifacts_and_blackboard_entries(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    recorder = CrewRecorder(repo_root / ".orchestrator")
    pool = FakeWorkerPool()
    controller = CrewController(
        recorder=recorder,
        blackboard=BlackboardStore(recorder),
        task_graph=TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}"),
        worker_pool=pool,
        crew_id_factory=lambda: "crew-1",
        entry_id_factory=lambda: "entry-gate",
    )
    crew = controller.start_dynamic(repo_root=repo_root, goal="Harden dynamic crew")

    artifact = controller.write_json_artifact(
        crew_id=crew.crew_id,
        artifact_name="gates/round-1/write_scope.json",
        payload={"status": "pass"},
    )
    entry = controller.record_blackboard_entry(
        crew_id=crew.crew_id,
        entry_type=BlackboardEntryType.DECISION,
        content="Readiness evaluated",
        evidence_refs=[artifact],
    )
    details = recorder.read_crew(crew.crew_id)

    assert artifact == "gates/round-1/write_scope.json"
    assert "gates/round-1/write_scope.json" in details["artifacts"]
    assert entry["type"] == "decision"
    assert details["blackboard"][-1]["content"] == "Readiness evaluated"
    assert details["blackboard"][-1]["evidence_refs"] == ["gates/round-1/write_scope.json"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_controller.py::test_controller_records_gate_artifacts_and_blackboard_entries -q
```

Expected: FAIL with `AttributeError: 'CrewController' object has no attribute 'write_json_artifact'`.

- [ ] **Step 3: Add controller helper methods**

In `src/codex_claude_orchestrator/crew/controller.py`, add these methods after `append_known_pitfall()`:

```python
    def write_json_artifact(self, *, crew_id: str, artifact_name: str, payload) -> str:
        self._recorder.write_json_artifact(crew_id, artifact_name, payload)
        return artifact_name

    def record_blackboard_entry(
        self,
        *,
        crew_id: str,
        entry_type: BlackboardEntryType | str,
        content: str,
        evidence_refs: list[str] | None = None,
        task_id: str | None = None,
        actor_type: ActorType | str = ActorType.CODEX,
        actor_id: str = "codex",
        confidence: float = 1.0,
    ) -> dict:
        entry = BlackboardEntry(
            entry_id=self._entry_id_factory(),
            crew_id=crew_id,
            task_id=task_id,
            actor_type=ActorType(actor_type),
            actor_id=actor_id,
            type=BlackboardEntryType(entry_type),
            content=content,
            evidence_refs=evidence_refs or [],
            confidence=confidence,
        )
        self._blackboard.append(entry)
        return entry.to_dict()
```

- [ ] **Step 4: Run controller helper test**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_controller.py::test_controller_records_gate_artifacts_and_blackboard_entries -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit controller helper task**

```bash
git add src/codex_claude_orchestrator/crew/controller.py tests/crew/test_controller.py
git commit -m "feat: record crew gate artifacts"
```

## Task 6: Supervisor Scope Gate Integration

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/supervisor_loop.py`
- Modify: `tests/crew/test_supervisor_loop.py`

- [ ] **Step 1: Extend `FakeController` for artifacts**

In `tests/crew/test_supervisor_loop.py`, update `FakeController.__init__`:

```python
        self.artifacts = []
        self.blackboard_entries = []
```

Add methods to `FakeController`:

```python
    def write_json_artifact(self, **kwargs):
        self.artifacts.append(kwargs)
        return kwargs["artifact_name"]

    def record_blackboard_entry(self, **kwargs):
        self.blackboard_entries.append(kwargs)
        return {
            "type": kwargs["entry_type"].value if hasattr(kwargs["entry_type"], "value") else kwargs["entry_type"],
            "content": kwargs["content"],
            "evidence_refs": kwargs.get("evidence_refs", []),
        }
```

Update `FakeController.ensure_worker()` worker payload to include `write_scope`:

```python
            "write_scope": kwargs["contract"].write_scope,
```

- [ ] **Step 2: Write failing scope integration tests**

Append to `tests/crew/test_supervisor_loop.py`:

```python
def test_supervisor_loop_dynamic_challenges_out_of_scope_low_risk_change(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Fix source"}, "workers": []}

    def out_of_scope_changes(**kwargs):
        controller.changes_called.append(kwargs)
        return {
            "worker_id": kwargs["worker_id"],
            "changed_files": ["docs/notes.md"],
            "artifact": "workers/worker-source/changes.json",
            "diff_artifact": "workers/worker-source/diff.patch",
        }

    controller.changes = out_of_scope_changes
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Fix source",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "max_rounds_exhausted"
    assert controller.verify_called == []
    assert controller.challenge_called[0]["summary"].startswith("Changed files outside write_scope")
    assert any(item["artifact_name"] == "gates/round-1/write_scope.json" for item in controller.artifacts)


def test_supervisor_loop_dynamic_blocks_protected_scope_violation(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Fix source"}, "workers": []}

    def protected_changes(**kwargs):
        controller.changes_called.append(kwargs)
        return {
            "worker_id": kwargs["worker_id"],
            "changed_files": ["pyproject.toml"],
            "artifact": "workers/worker-source/changes.json",
            "diff_artifact": "workers/worker-source/diff.patch",
        }

    controller.changes = protected_changes
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Fix source",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "needs_human"
    assert result["reason"] == "write_scope_blocked"
    assert result["readiness_artifact"] == "readiness/round-1.json"
    assert controller.verify_called == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_challenges_out_of_scope_low_risk_change tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_blocks_protected_scope_violation -q
```

Expected: FAIL because supervisor still runs verification and returns `ready_for_codex_accept`.

- [ ] **Step 4: Add scope gate integration helpers**

In `src/codex_claude_orchestrator/crew/supervisor_loop.py`, add imports:

```python
from codex_claude_orchestrator.crew.gates import GateResult, WriteScopeGate
from codex_claude_orchestrator.crew.readiness import CrewReadinessEvaluator
```

Update `CrewSupervisorLoop.__init__` signature and body:

```python
        scope_gate: WriteScopeGate | None = None,
        readiness_evaluator: CrewReadinessEvaluator | None = None,
```

```python
        self._scope_gate = scope_gate or WriteScopeGate()
        self._readiness_evaluator = readiness_evaluator or CrewReadinessEvaluator()
```

Add helper methods near `_record_decision_if_supported()`:

```python
    def _write_json_artifact_if_supported(self, crew_id: str, artifact_name: str, payload: dict[str, Any]) -> str:
        writer = getattr(self._controller, "write_json_artifact", None)
        if writer is not None:
            return writer(crew_id=crew_id, artifact_name=artifact_name, payload=payload)
        return artifact_name

    def _record_blackboard_if_supported(
        self,
        *,
        crew_id: str,
        entry_type: str,
        content: str,
        evidence_refs: list[str] | None = None,
    ) -> None:
        recorder = getattr(self._controller, "record_blackboard_entry", None)
        if recorder is not None:
            recorder(
                crew_id=crew_id,
                entry_type=entry_type,
                content=content,
                evidence_refs=evidence_refs or [],
            )

    def _write_scope_for_worker(self, details: dict[str, Any], worker: dict[str, Any], fallback_scope: list[str]) -> list[str]:
        if worker.get("write_scope"):
            return list(worker["write_scope"])
        contract_id = worker.get("contract_id")
        for contract in details.get("worker_contracts", []):
            if contract.get("contract_id") == contract_id:
                return list(contract.get("write_scope") or fallback_scope)
        return list(fallback_scope)

    def _write_readiness_report(
        self,
        *,
        crew_id: str,
        round_index: int,
        source_worker: dict[str, Any],
        changes: dict[str, Any],
        scope_result: GateResult,
        review_verdict=None,
        verification_results: list[dict[str, Any]] | None = None,
    ):
        report = self._readiness_evaluator.evaluate(
            round_id=f"round-{round_index}",
            worker_id=source_worker["worker_id"],
            contract_id=source_worker.get("contract_id", ""),
            changed_files=list(changes.get("changed_files", [])),
            scope_result=scope_result,
            review_verdict=review_verdict,
            verification_results=verification_results or [],
        )
        artifact = self._write_json_artifact_if_supported(
            crew_id,
            f"readiness/round-{round_index}.json",
            report.to_dict(),
        )
        self._record_blackboard_if_supported(
            crew_id=crew_id,
            entry_type="decision",
            content=f"Readiness {report.status}: {', '.join(report.blockers) if report.blockers else 'ready'}",
            evidence_refs=[artifact, *report.evidence_refs],
        )
        return report, artifact
```

- [ ] **Step 5: Insert scope gate after dynamic `changes()`**

In `supervise_dynamic()`, immediately after:

```python
            changes = self._controller.changes(crew_id=crew_id, worker_id=source_worker["worker_id"])
            events.append({"action": "record_changes", "changes": changes})
```

Insert:

```python
            latest_details = self._controller.status(repo_root=repo_root, crew_id=crew_id)
            source_write_scope = self._write_scope_for_worker(latest_details, source_worker, repo_write_scope)
            scope_result = self._scope_gate.evaluate(
                changed_files=list(changes.get("changed_files", [])),
                write_scope=source_write_scope,
                evidence_refs=[ref for ref in [changes.get("artifact"), changes.get("diff_artifact")] if ref],
            )
            scope_artifact = self._write_json_artifact_if_supported(
                crew_id,
                f"gates/round-{round_index}/write_scope.json",
                scope_result.to_dict(),
            )
            events.append(
                {
                    "action": "write_scope_gate",
                    "round": round_index,
                    "status": scope_result.status,
                    "reason": scope_result.reason,
                    "artifact": scope_artifact,
                }
            )
            if scope_result.status == "block":
                readiness, readiness_artifact = self._write_readiness_report(
                    crew_id=crew_id,
                    round_index=round_index,
                    source_worker=source_worker,
                    changes=changes,
                    scope_result=scope_result,
                )
                return {
                    "crew_id": crew_id,
                    "status": "needs_human",
                    "reason": "write_scope_blocked",
                    "rounds": round_index,
                    "events": events,
                    "readiness_artifact": readiness_artifact,
                    "readiness_status": readiness.status,
                }
            if scope_result.status == "challenge":
                summary = self._scope_challenge_message(scope_result)
                self._controller.challenge(crew_id=crew_id, summary=summary)
                events.append({"action": "challenge", "round": round_index, "summary": summary})
                continue
```

Add helper:

```python
    def _scope_challenge_message(self, scope_result: GateResult) -> str:
        out_of_scope = scope_result.details.get("out_of_scope", [])
        write_scope = scope_result.details.get("write_scope", [])
        changed = "\n".join(f"- {path}" for path in out_of_scope) or "- no out-of-scope paths recorded"
        allowed = "\n".join(f"- {path}" for path in write_scope) or "- no write scope recorded"
        return (
            "Changed files outside write_scope:\n"
            f"{changed}\n\n"
            "Allowed write_scope:\n"
            f"{allowed}\n\n"
            "Revert the out-of-scope edits or request an expanded scope using CODEX_MESSAGE."
        )
```

- [ ] **Step 6: Run scope integration tests**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_challenges_out_of_scope_low_risk_change tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_blocks_protected_scope_violation -q
```

Expected: `2 passed`.

- [ ] **Step 7: Commit scope integration task**

```bash
git add src/codex_claude_orchestrator/crew/supervisor_loop.py tests/crew/test_supervisor_loop.py
git commit -m "feat: enforce dynamic crew write scope"
```

## Task 7: Supervisor Review Verdict Integration

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/supervisor_loop.py`
- Modify: `tests/crew/test_supervisor_loop.py`

- [ ] **Step 1: Make fake reviewer output parseable by default**

Update `FakeController.observe_worker()` in `tests/crew/test_supervisor_loop.py`:

```python
    def observe_worker(self, **kwargs):
        self.observed.append(kwargs)
        marker = kwargs.get("turn_marker") or "<<<CODEX_TURN_DONE status=ready_for_codex>>>"
        snapshot = f"{kwargs['worker_id']} done\n{marker}"
        if kwargs["worker_id"] == "worker-patch-risk-auditor":
            snapshot = (
                "review complete\n"
                "<<<CODEX_REVIEW\n"
                "verdict: OK\n"
                "summary: Patch looks safe.\n"
                "findings:\n"
                "- Tests cover the changed path.\n"
                ">>>\n"
                f"{marker}"
            )
        return {"snapshot": snapshot, "marker_seen": True}
```

- [ ] **Step 2: Write failing review gate tests**

Append to `tests/crew/test_supervisor_loop.py`:

```python
def test_supervisor_loop_dynamic_review_block_challenges_source_and_skips_verification(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Refactor public API"}, "workers": []}

    def block_review(**kwargs):
        controller.observed.append(kwargs)
        marker = kwargs.get("turn_marker")
        if kwargs["worker_id"] == "worker-patch-risk-auditor":
            return {
                "snapshot": (
                    "<<<CODEX_REVIEW\n"
                    "verdict: BLOCK\n"
                    "summary: Regression in public API.\n"
                    "findings:\n"
                    "- The compatibility shim was removed.\n"
                    ">>>\n"
                    f"{marker}"
                ),
                "marker_seen": True,
            }
        return {"snapshot": f"{kwargs['worker_id']} done\n{marker}", "marker_seen": True}

    controller.observe_worker = block_review
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Refactor public API",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "max_rounds_exhausted"
    assert controller.verify_called == []
    assert "Regression in public API" in controller.challenge_called[0]["summary"]
    assert any(item["artifact_name"] == "workers/worker-patch-risk-auditor/review_verdict.json" for item in controller.artifacts)


def test_supervisor_loop_dynamic_unknown_review_returns_needs_human(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Refactor public API"}, "workers": []}

    def unknown_review(**kwargs):
        controller.observed.append(kwargs)
        marker = kwargs.get("turn_marker")
        if kwargs["worker_id"] == "worker-patch-risk-auditor":
            return {"snapshot": f"Looks fine without structured verdict\n{marker}", "marker_seen": True}
        return {"snapshot": f"{kwargs['worker_id']} done\n{marker}", "marker_seen": True}

    controller.observe_worker = unknown_review
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Refactor public API",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "needs_human"
    assert result["reason"] == "review_verdict_unknown"
    assert controller.verify_called == []
    assert result["readiness_artifact"] == "readiness/round-1.json"
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_review_block_challenges_source_and_skips_verification tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_unknown_review_returns_needs_human -q
```

Expected: FAIL because supervisor still sets `review_status = "ok"`.

- [ ] **Step 4: Add review parser integration**

In `src/codex_claude_orchestrator/crew/supervisor_loop.py`, add import:

```python
from codex_claude_orchestrator.crew.review_verdict import ReviewVerdictParser
```

Update `__init__`:

```python
        review_parser: ReviewVerdictParser | None = None,
```

```python
        self._review_parser = review_parser or ReviewVerdictParser()
```

In `supervise_dynamic()`, immediately after:

```python
            review_status = None
```

insert:

```python
            review_verdict = None
```

In `supervise_dynamic()`, replace:

```python
                    review_status = "ok"
```

with:

```python
                    review_verdict = self._review_parser.parse(
                        auditor_observation.get("snapshot", ""),
                        evidence_refs=[auditor.get("transcript_artifact", "")] if auditor.get("transcript_artifact") else [],
                        raw_artifact=auditor.get("transcript_artifact", ""),
                    )
                    review_artifact = self._write_json_artifact_if_supported(
                        crew_id,
                        f"workers/{auditor['worker_id']}/review_verdict.json",
                        review_verdict.to_dict(),
                    )
                    events.append(
                        {
                            "action": "review_verdict_parsed",
                            "round": round_index,
                            "worker_id": auditor["worker_id"],
                            "status": review_verdict.status,
                            "artifact": review_artifact,
                        }
                    )
                    self._record_blackboard_if_supported(
                        crew_id=crew_id,
                        entry_type="review",
                        content=f"Review verdict {review_verdict.status}: {review_verdict.summary}",
                        evidence_refs=[review_artifact, *review_verdict.evidence_refs],
                    )
                    if review_verdict.status == "unknown":
                        readiness, readiness_artifact = self._write_readiness_report(
                            crew_id=crew_id,
                            round_index=round_index,
                            source_worker=source_worker,
                            changes=changes,
                            scope_result=scope_result,
                            review_verdict=review_verdict,
                        )
                        return {
                            "crew_id": crew_id,
                            "status": "needs_human",
                            "reason": "review_verdict_unknown",
                            "rounds": round_index,
                            "events": events,
                            "readiness_artifact": readiness_artifact,
                            "readiness_status": readiness.status,
                        }
                    if review_verdict.status == "block":
                        summary = self._review_challenge_message(review_verdict)
                        self._controller.challenge(crew_id=crew_id, summary=summary)
                        events.append({"action": "challenge", "round": round_index, "summary": summary})
                        continue
                    review_status = review_verdict.status
```

Add helper:

```python
    def _review_challenge_message(self, review_verdict) -> str:
        findings = "\n".join(f"- {finding}" for finding in review_verdict.findings) or "- no individual findings provided"
        return (
            f"Review BLOCK: {review_verdict.summary}\n\n"
            "Findings:\n"
            f"{findings}\n\n"
            "Fix these review findings before verification."
        )
```

- [ ] **Step 5: Update review message to demand verdict**

Replace `_review_message()` return with:

```python
        return (
            "Review the implementer patch. "
            f"Changed files: {changed_files}. Diff artifact: {diff_artifact}\n\n"
            "Return a parseable review block exactly in this shape:\n"
            "<<<CODEX_REVIEW\n"
            "verdict: OK | WARN | BLOCK\n"
            "summary: one sentence\n"
            "findings:\n"
            "- finding text\n"
            ">>>\n"
            "Use BLOCK for correctness regressions, unsafe scope, or missing critical tests."
        )
```

- [ ] **Step 6: Run review integration tests**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_review_block_challenges_source_and_skips_verification tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_unknown_review_returns_needs_human -q
```

Expected: `2 passed`.

- [ ] **Step 7: Run existing dynamic supervisor tests**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_supervisor_loop.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit review integration task**

```bash
git add src/codex_claude_orchestrator/crew/supervisor_loop.py tests/crew/test_supervisor_loop.py
git commit -m "feat: gate dynamic crew review verdicts"
```

## Task 8: Readiness Integration for Verification Success and Failure

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/supervisor_loop.py`
- Modify: `tests/crew/test_supervisor_loop.py`

- [ ] **Step 1: Write failing readiness integration tests**

Append to `tests/crew/test_supervisor_loop.py`:

```python
def test_supervisor_loop_dynamic_ready_result_includes_readiness_artifact(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0", "stdout_artifact": "stdout.txt"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Refactor public API"}, "workers": []}
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Refactor public API",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "ready_for_codex_accept"
    assert result["readiness_artifact"] == "readiness/round-1.json"
    readiness_payload = next(item["payload"] for item in controller.artifacts if item["artifact_name"] == "readiness/round-1.json")
    assert readiness_payload["status"] == "ready"
    assert readiness_payload["verification_status"] == "pass"


def test_supervisor_loop_dynamic_verification_failure_writes_readiness_artifact(tmp_path: Path):
    controller = FakeController([{"passed": False, "summary": "command failed: exit code 1", "stderr_artifact": "stderr.txt"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Refactor public API"}, "workers": []}
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Refactor public API",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "max_rounds_exhausted"
    readiness_payload = next(item["payload"] for item in controller.artifacts if item["artifact_name"] == "readiness/round-1.json")
    assert readiness_payload["status"] == "challenge"
    assert readiness_payload["verification_status"] == "fail"
    assert "command failed: exit code 1" in readiness_payload["blockers"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_ready_result_includes_readiness_artifact tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_verification_failure_writes_readiness_artifact -q
```

Expected: FAIL because ready payload and verification failure path do not write readiness artifacts yet.

- [ ] **Step 3: Write readiness after verification**

In `supervise_dynamic()`, after verification results are appended:

```python
            verification_results = [
                self._controller.verify(
                    crew_id=crew_id,
                    command=command,
                    worker_id=source_worker["worker_id"],
                )
                for command in verification_commands
            ]
            events.append({"action": "verify", "round": round_index, "results": verification_results})
```

Insert:

```python
            readiness, readiness_artifact = self._write_readiness_report(
                crew_id=crew_id,
                round_index=round_index,
                source_worker=source_worker,
                changes=changes,
                scope_result=scope_result,
                review_verdict=review_verdict,
                verification_results=verification_results,
            )
            events.append(
                {
                    "action": "readiness_evaluated",
                    "round": round_index,
                    "status": readiness.status,
                    "artifact": readiness_artifact,
                }
            )
```

In the `if not failed:` return payload, add:

```python
                    "readiness_artifact": readiness_artifact,
                    "warnings": readiness.warnings,
```

- [ ] **Step 4: Ensure verification failure keeps existing challenge flow**

No separate code block is needed beyond Step 3. The existing failed verification branch remains in place and now has a readiness artifact available before challenge handling continues.

- [ ] **Step 5: Run readiness integration tests**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_ready_result_includes_readiness_artifact tests/crew/test_supervisor_loop.py::test_supervisor_loop_dynamic_verification_failure_writes_readiness_artifact -q
```

Expected: `2 passed`.

- [ ] **Step 6: Run full crew supervisor tests**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_supervisor_loop.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit readiness integration task**

```bash
git add src/codex_claude_orchestrator/crew/supervisor_loop.py tests/crew/test_supervisor_loop.py
git commit -m "feat: require dynamic crew readiness evidence"
```

## Task 9: Marker Policy Integration

**Files:**
- Modify: `src/codex_claude_orchestrator/crew/supervisor_loop.py`
- Modify: `tests/crew/test_supervisor_loop.py`

- [ ] **Step 1: Write failing marker integration test**

Append to `tests/crew/test_supervisor_loop.py`:

```python
def test_supervisor_loop_waiting_result_includes_marker_mismatch_reason(tmp_path: Path):
    controller = FakeController([{"passed": True, "summary": "command passed: exit code 0"}])
    controller.status_payload = {"crew": {"crew_id": "crew-1", "root_goal": "Fix tests"}, "workers": []}

    def mismatched_observe(**kwargs):
        controller.observed.append(kwargs)
        return {
            "snapshot": "<<<CODEX_TURN_DONE crew=crew-1 contract=source_write>>>",
            "marker_seen": False,
            "marker": kwargs.get("turn_marker"),
            "transcript_artifact": "workers/worker-source/transcript.txt",
        }

    controller.observe_worker = mismatched_observe
    loop = CrewSupervisorLoop(controller=controller, poll_interval_seconds=0, max_observe_attempts=1)

    result = loop.run(
        repo_root=tmp_path,
        goal="Fix tests",
        verification_commands=["pytest -q"],
        max_rounds=1,
        spawn_policy="dynamic",
    )

    assert result["status"] == "waiting_for_worker"
    assert result["reason"] == "contract marker found but expected turn marker was missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_supervisor_loop.py::test_supervisor_loop_waiting_result_includes_marker_mismatch_reason -q
```

Expected: FAIL because `_waiting_result()` does not include reason.

- [ ] **Step 3: Integrate marker policy**

In `src/codex_claude_orchestrator/crew/supervisor_loop.py`, add import:

```python
from codex_claude_orchestrator.runtime.marker_policy import MarkerObservationPolicy
```

Update `__init__`:

```python
        marker_policy: MarkerObservationPolicy | None = None,
```

```python
        self._marker_policy = marker_policy or MarkerObservationPolicy()
```

Replace `_wait_for_marker()` body with:

```python
        last_observation: dict[str, Any] = {"marker_seen": False, "snapshot": ""}
        for attempt in range(self._max_observe_attempts):
            last_observation = self._controller.observe_worker(
                repo_root=repo_root,
                crew_id=crew_id,
                worker_id=worker_id,
                lines=200,
                turn_marker=turn_marker,
            )
            marker = turn_marker or last_observation.get("marker", "")
            policy_observation = self._marker_policy.evaluate(
                snapshot=last_observation.get("snapshot", ""),
                expected_marker=marker,
                transcript_text=last_observation.get("transcript", ""),
                transcript_artifact=last_observation.get("transcript_artifact", ""),
                contract_marker=f"<<<CODEX_TURN_DONE crew={crew_id} contract=source_write>>>",
            )
            last_observation = {
                **last_observation,
                **policy_observation.to_dict(),
                "marker_seen": policy_observation.marker_seen,
            }
            if policy_observation.marker_seen:
                return last_observation
            if interval > 0 and attempt + 1 < self._max_observe_attempts:
                time.sleep(interval)
        return last_observation
```

Replace `_waiting_result()` with:

```python
    def _waiting_result(self, crew_id: str, worker_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        reason = "expected marker not found"
        if events:
            last_event = events[-1]
            reason = last_event.get("reason") or reason
        return {"crew_id": crew_id, "status": "waiting_for_worker", "worker_id": worker_id, "reason": reason, "events": events}
```

Update every dynamic-path observe event dictionary whose `"action"` is `"observe_worker"` to include:

```python
                            "reason": scout_observation.get("reason", ""),
```

Use the relevant observation variable for each observe event: `scout_observation`, `observation`, `auditor_observation`, and `browser_observation`.

- [ ] **Step 4: Run marker integration test**

Run:

```bash
.venv/bin/python -m pytest tests/crew/test_supervisor_loop.py::test_supervisor_loop_waiting_result_includes_marker_mismatch_reason -q
```

Expected: `1 passed`.

- [ ] **Step 5: Run runtime marker tests and supervisor tests**

Run:

```bash
.venv/bin/python -m pytest tests/runtime/test_marker_policy.py tests/crew/test_supervisor_loop.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit marker integration task**

```bash
git add src/codex_claude_orchestrator/crew/supervisor_loop.py tests/crew/test_supervisor_loop.py
git commit -m "feat: explain dynamic crew marker waits"
```

## Task 10: Final Regression and Spec Traceability

**Files:**
- Verify: all files touched in Tasks 1-9

- [ ] **Step 1: Run targeted test suite**

Run:

```bash
.venv/bin/python -m pytest tests/crew tests/runtime/test_marker_policy.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass. If unrelated tests fail due to the existing repository refactor, record the failing test names and rerun the targeted tests from Step 1 to confirm this feature is green.

- [ ] **Step 3: Inspect changed files**

Run:

```bash
git status --short
git diff --stat
```

Expected: only files from this plan are modified relative to the task branch.

- [ ] **Step 4: Check spec coverage manually**

Verify these mappings:

```text
Review verdict gate -> Tasks 1 and 7
Write scope enforcement -> Tasks 2 and 6
Readiness report -> Tasks 3 and 8
Marker robustness -> Tasks 4 and 9
Controller persistence helpers -> Task 5
No bridge/session/dispatch changes -> git diff paths stay outside those packages
```

- [ ] **Step 5: Commit final cleanup if Step 2 or Step 3 required changes**

If no cleanup changes were made, skip this commit. If cleanup changes were made, run:

```bash
git add src/codex_claude_orchestrator/crew src/codex_claude_orchestrator/runtime tests/crew tests/runtime
git commit -m "test: verify dynamic crew reliability gates"
```

## Self-Review Notes

Spec coverage:

- Review verdict parsing is implemented by Tasks 1 and 7.
- `write_scope` diff gate is implemented by Tasks 2 and 6.
- Readiness artifacts and ready payload changes are implemented by Tasks 3 and 8.
- Marker mismatch and transcript fallback are implemented by Tasks 4 and 9.
- CLI behavior is covered through `crew run` payload tests in Task 8 and waiting reason tests in Task 9.
- Non-goals are preserved because no tasks modify `src/codex_claude_orchestrator/bridge`, `src/codex_claude_orchestrator/session`, or single worker dispatch code.

Type consistency:

- `ReviewVerdict.status` uses `ok`, `warn`, `block`, `unknown`.
- `GateResult.status` uses `pass`, `challenge`, `block`.
- `ReadinessReport.status` uses `ready`, `challenge`, `blocked`.
- Supervisor return status remains existing public strings: `ready_for_codex_accept`, `needs_human`, `waiting_for_worker`, and `max_rounds_exhausted`.
