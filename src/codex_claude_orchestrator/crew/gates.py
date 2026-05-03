from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from codex_claude_orchestrator.crew.scope import is_protected as _is_protected_path, scope_covers


DEFAULT_PROTECTED_PATTERNS = [
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


def _normalize_path(path: str | Path) -> str:
    normalized = str(path).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


@dataclass(slots=True)
class GateResult:
    status: str
    reason: str
    evidence_refs: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _normalize(self)


class WriteScopeGate:
    def __init__(self, protected_patterns: list[str] | None = None) -> None:
        patterns = DEFAULT_PROTECTED_PATTERNS if protected_patterns is None else protected_patterns
        self.protected_patterns = [
            _normalize_path(pattern)
            for pattern in patterns
        ]

    def evaluate(
        self,
        *,
        changed_files: list[str | Path],
        write_scope: list[str | Path],
        evidence_refs: list[str] | None = None,
    ) -> GateResult:
        normalized_changed_files = [_normalize_path(path) for path in changed_files]
        normalized_write_scope = [_normalize_path(path) for path in write_scope if _normalize_path(path)]
        refs = list(evidence_refs or [])

        if not normalized_changed_files:
            return GateResult(status="pass", reason="no changed files", evidence_refs=refs)

        out_of_scope = [
            path
            for path in normalized_changed_files
            if not self._is_in_scope(path, normalized_write_scope)
        ]

        if not normalized_write_scope:
            return GateResult(
                status="block",
                reason="write_scope is empty but files changed",
                evidence_refs=refs,
                details={"out_of_scope": out_of_scope},
            )

        protected = [path for path in out_of_scope if self._is_protected(path)]
        details = {"out_of_scope": out_of_scope, "protected": protected}

        if protected:
            return GateResult(
                status="block",
                reason="protected files changed outside write_scope",
                evidence_refs=refs,
                details=details,
            )

        if out_of_scope:
            return GateResult(
                status="challenge",
                reason="changed files are outside write_scope",
                evidence_refs=refs,
                details=details,
            )

        return GateResult(
            status="pass",
            reason="all changed files are inside write_scope",
            evidence_refs=refs,
            details=details,
        )

    def _is_in_scope(self, path: str, write_scope: list[str]) -> bool:
        return scope_covers(write_scope, path)

    def _is_protected(self, path: str) -> bool:
        return _is_protected_path(path, self.protected_patterns)
