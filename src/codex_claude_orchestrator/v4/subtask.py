"""SubTask data model for the parallel supervisor feature."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SubTask:
    """A unit of work decomposed from a parent task for parallel execution.

    Status lifecycle: pending -> running -> unit_review -> passed | failed
    """

    task_id: str
    description: str
    scope: list[str]
    depends_on: list[str] = field(default_factory=list)
    worker_id: str = ""
    status: str = "pending"
    result: dict[str, Any] | None = None
    review_attempts: int = 0
    # Long task extensions
    role: str = ""
    goal: str = ""
    write_scope: list[str] = field(default_factory=list)
    worker_template: str = "targeted-code-editor"

    def to_dict(self) -> dict[str, Any]:
        """Serialize all fields to a plain dict."""
        return {
            "task_id": self.task_id,
            "description": self.description,
            "scope": list(self.scope),
            "depends_on": list(self.depends_on),
            "worker_id": self.worker_id,
            "status": self.status,
            "result": self.result,
            "review_attempts": self.review_attempts,
            "role": self.role,
            "goal": self.goal,
            "write_scope": list(self.write_scope),
            "worker_template": self.worker_template,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubTask:
        """Create a SubTask from a dict, applying defaults for missing keys."""
        return cls(
            task_id=data["task_id"],
            description=data["description"],
            scope=data["scope"],
            depends_on=data.get("depends_on", []),
            worker_id=data.get("worker_id", ""),
            status=data.get("status", "pending"),
            result=data.get("result"),
            review_attempts=data.get("review_attempts", 0),
            role=data.get("role", ""),
            goal=data.get("goal", ""),
            write_scope=data.get("write_scope", []),
            worker_template=data.get("worker_template", "targeted-code-editor"),
        )
