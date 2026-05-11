"""Data models for the long task adversarial agent system.

These models define the contract between phases:
- ThinkResult: brainstorming output -> PlanAdversary input
- StagePlan: stage definition with sub-tasks and contracts
- Briefing: context injection for Workers and Reviewers
- ReviewVerdict: Reviewer output (pass/challenge/replan)
- PlanAdversaryVerdict: Plan Adversary output (pass/fix/reject)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# --- Contract models ---


@dataclass(slots=True)
class ApiSpec:
    """API endpoint specification."""

    method: str
    path: str
    request_body: dict[str, Any] | None = None
    response_body: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"method": self.method, "path": self.path}
        if self.request_body is not None:
            d["request_body"] = self.request_body
        if self.response_body:
            d["response_body"] = self.response_body
        if self.description:
            d["description"] = self.description
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApiSpec:
        return cls(
            method=data["method"],
            path=data["path"],
            request_body=data.get("request_body"),
            response_body=data.get("response_body", {}),
            description=data.get("description", ""),
        )


@dataclass(slots=True)
class DataModel:
    """Data model specification."""

    name: str
    fields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "fields": dict(self.fields)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataModel:
        return cls(name=data["name"], fields=data.get("fields", {}))


@dataclass(slots=True)
class Contract:
    """Interface contract between sub-tasks."""

    api_endpoints: list[ApiSpec] = field(default_factory=list)
    data_models: list[DataModel] = field(default_factory=list)
    shared_types: list[str] = field(default_factory=list)
    conventions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_endpoints": [a.to_dict() for a in self.api_endpoints],
            "data_models": [d.to_dict() for d in self.data_models],
            "shared_types": list(self.shared_types),
            "conventions": list(self.conventions),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Contract:
        return cls(
            api_endpoints=[ApiSpec.from_dict(a) for a in data.get("api_endpoints", [])],
            data_models=[DataModel.from_dict(d) for d in data.get("data_models", [])],
            shared_types=data.get("shared_types", []),
            conventions=data.get("conventions", []),
        )


# --- Project context ---


@dataclass(slots=True)
class ProjectContext:
    """Project context gathered during brainstorming."""

    structure: str = ""
    existing_patterns: list[str] = field(default_factory=list)
    tech_stack: list[str] = field(default_factory=list)
    related_files: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "structure": self.structure,
            "existing_patterns": list(self.existing_patterns),
            "tech_stack": list(self.tech_stack),
            "related_files": list(self.related_files),
            "constraints": list(self.constraints),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectContext:
        return cls(
            structure=data.get("structure", ""),
            existing_patterns=data.get("existing_patterns", []),
            tech_stack=data.get("tech_stack", []),
            related_files=data.get("related_files", []),
            constraints=data.get("constraints", []),
        )


# --- Sub-task reference (used in StagePlan) ---


@dataclass(slots=True)
class SubTaskRef:
    """Lightweight sub-task reference used in StagePlan.

    This is distinct from the existing SubTask (which tracks execution state).
    SubTaskRef is a planning-level reference with role, goal, write_scope.
    """

    task_id: str
    role: str
    goal: str
    dependencies: list[str] = field(default_factory=list)
    write_scope: list[str] = field(default_factory=list)
    worker_template: str = "targeted-code-editor"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "role": self.role,
            "goal": self.goal,
            "dependencies": list(self.dependencies),
            "write_scope": list(self.write_scope),
            "worker_template": self.worker_template,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubTaskRef:
        return cls(
            task_id=data["task_id"],
            role=data["role"],
            goal=data["goal"],
            dependencies=data.get("dependencies", []),
            write_scope=data.get("write_scope", []),
            worker_template=data.get("worker_template", "targeted-code-editor"),
        )


# --- Stage plan ---


@dataclass(slots=True)
class StagePlan:
    """A single stage in the long task execution plan."""

    stage_id: int
    goal: str
    acceptance_criteria: list[str]
    contract: Contract
    sub_tasks: list[SubTaskRef]
    dependencies: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "goal": self.goal,
            "acceptance_criteria": list(self.acceptance_criteria),
            "contract": self.contract.to_dict(),
            "sub_tasks": [s.to_dict() for s in self.sub_tasks],
            "dependencies": list(self.dependencies),
        }

    def to_event_dict(self) -> dict[str, Any]:
        """Dict suitable for EventStore payload."""
        return self.to_dict()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StagePlan:
        return cls(
            stage_id=data["stage_id"],
            goal=data["goal"],
            acceptance_criteria=data.get("acceptance_criteria", []),
            contract=Contract.from_dict(data.get("contract", {})),
            sub_tasks=[SubTaskRef.from_dict(s) for s in data.get("sub_tasks", [])],
            dependencies=data.get("dependencies", []),
        )


# --- Think result (brainstorming output) ---


@dataclass(slots=True)
class ThinkResult:
    """Structured output from the brainstorming phase."""

    spec: str
    stages: list[StagePlan]
    contract: Contract
    project_context: ProjectContext
    acceptance_criteria: list[str]
    open_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec,
            "stages": [s.to_dict() for s in self.stages],
            "contract": self.contract.to_dict(),
            "project_context": self.project_context.to_dict(),
            "acceptance_criteria": list(self.acceptance_criteria),
            "open_questions": list(self.open_questions),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThinkResult:
        return cls(
            spec=data["spec"],
            stages=[StagePlan.from_dict(s) for s in data["stages"]],
            contract=Contract.from_dict(data["contract"]),
            project_context=ProjectContext.from_dict(data["project_context"]),
            acceptance_criteria=data["acceptance_criteria"],
            open_questions=data.get("open_questions", []),
        )


# --- Briefing (context injection) ---


@dataclass(slots=True)
class Briefing:
    """Context package injected into Worker and Reviewer prompts."""

    overall_goal: str
    current_stage: StagePlan
    contract: Contract
    previous_summaries: list[str]
    key_decisions: list[str]
    constraints: list[str]
    pending_questions: list[str]
    verification_commands: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_goal": self.overall_goal,
            "current_stage": self.current_stage.to_dict(),
            "contract": self.contract.to_dict(),
            "previous_summaries": list(self.previous_summaries),
            "key_decisions": list(self.key_decisions),
            "constraints": list(self.constraints),
            "pending_questions": list(self.pending_questions),
            "verification_commands": list(self.verification_commands),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Briefing:
        return cls(
            overall_goal=data["overall_goal"],
            current_stage=StagePlan.from_dict(data["current_stage"]),
            contract=Contract.from_dict(data["contract"]),
            previous_summaries=data.get("previous_summaries", []),
            key_decisions=data.get("key_decisions", []),
            constraints=data.get("constraints", []),
            pending_questions=data.get("pending_questions", []),
            verification_commands=data.get("verification_commands", []),
        )


# --- Review verdict ---


@dataclass(slots=True)
class CheckItem:
    """Single check result in a review."""

    criterion: str
    status: str  # "pass" | "fail"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"criterion": self.criterion, "status": self.status, "note": self.note}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckItem:
        return cls(criterion=data["criterion"], status=data["status"], note=data.get("note", ""))


@dataclass(slots=True)
class ChallengeTarget:
    """A Worker that needs to be challenged."""

    worker_id: str
    challenge_message: str
    affected_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "challenge_message": self.challenge_message,
            "affected_files": list(self.affected_files),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChallengeTarget:
        return cls(
            worker_id=data["worker_id"],
            challenge_message=data["challenge_message"],
            affected_files=data.get("affected_files", []),
        )


@dataclass(slots=True)
class ReviewVerdict:
    """Reviewer output combining unit review, integration review, and decision."""

    verdict: str  # "OK" | "WARN" | "BLOCK"
    checklist: list[CheckItem]
    quality_notes: list[str]
    risks: list[str]
    suggestions: list[str]
    contract_compliance: list[CheckItem]
    cross_worker_issues: list[str]
    action: str  # "pass" | "challenge" | "replan"
    challenge_targets: list[ChallengeTarget] | None = None
    replan_reason: str | None = None
    stage_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "verdict": self.verdict,
            "checklist": [c.to_dict() for c in self.checklist],
            "quality_notes": list(self.quality_notes),
            "risks": list(self.risks),
            "suggestions": list(self.suggestions),
            "contract_compliance": [c.to_dict() for c in self.contract_compliance],
            "cross_worker_issues": list(self.cross_worker_issues),
            "action": self.action,
        }
        if self.challenge_targets is not None:
            d["challenge_targets"] = [ct.to_dict() for ct in self.challenge_targets]
        if self.replan_reason is not None:
            d["replan_reason"] = self.replan_reason
        if self.stage_summary:
            d["stage_summary"] = self.stage_summary
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewVerdict:
        challenge_targets = None
        if "challenge_targets" in data and data["challenge_targets"] is not None:
            challenge_targets = [ChallengeTarget.from_dict(ct) for ct in data["challenge_targets"]]
        return cls(
            verdict=data["verdict"],
            checklist=[CheckItem.from_dict(c) for c in data.get("checklist", [])],
            quality_notes=data.get("quality_notes", []),
            risks=data.get("risks", []),
            suggestions=data.get("suggestions", []),
            contract_compliance=[CheckItem.from_dict(c) for c in data.get("contract_compliance", [])],
            cross_worker_issues=data.get("cross_worker_issues", []),
            action=data["action"],
            challenge_targets=challenge_targets,
            replan_reason=data.get("replan_reason"),
            stage_summary=data.get("stage_summary", ""),
        )


# --- Plan adversary verdict ---


@dataclass(slots=True)
class PlanIssue:
    """A single issue found by the Plan Adversary."""

    category: str  # "json", "coverage", "contract", "criteria", "logic", "scope", "feasibility"
    severity: str  # "block", "warn", "minor"
    location: str  # JSON path, e.g. "stages[1].contract.api_endpoints[0]"
    description: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "category": self.category,
            "severity": self.severity,
            "location": self.location,
            "description": self.description,
        }
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanIssue:
        return cls(
            category=data["category"],
            severity=data["severity"],
            location=data["location"],
            description=data["description"],
            suggestion=data.get("suggestion", ""),
        )


@dataclass(slots=True)
class AutoFix:
    """An automatic fix that can be applied to think_result.json."""

    location: str  # JSON path
    current_value: Any
    suggested_value: Any
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "location": self.location,
            "current_value": self.current_value,
            "suggested_value": self.suggested_value,
        }
        if self.reason:
            d["reason"] = self.reason
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AutoFix:
        return cls(
            location=data["location"],
            current_value=data.get("current_value"),
            suggested_value=data.get("suggested_value"),
            reason=data.get("reason", ""),
        )


@dataclass(slots=True)
class PlanAdversaryVerdict:
    """Plan Adversary output: pass, fix, or reject."""

    verdict: str  # "pass" | "fix" | "reject"
    issues: list[PlanIssue]
    auto_fixes: list[AutoFix]
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "verdict": self.verdict,
            "issues": [i.to_dict() for i in self.issues],
            "auto_fixes": [a.to_dict() for a in self.auto_fixes],
        }
        if self.summary:
            d["summary"] = self.summary
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanAdversaryVerdict:
        return cls(
            verdict=data["verdict"],
            issues=[PlanIssue.from_dict(i) for i in data.get("issues", [])],
            auto_fixes=[AutoFix.from_dict(a) for a in data.get("auto_fixes", [])],
            summary=data.get("summary", ""),
        )
