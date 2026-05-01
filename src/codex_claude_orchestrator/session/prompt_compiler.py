from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from codex_claude_orchestrator.core.models import TaskRecord


DEFAULT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "status": {"type": "string", "enum": ["completed", "needs_human", "failed"]},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "verification_commands": {"type": "array", "items": {"type": "string"}},
        "notes_for_supervisor": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "status",
        "changed_files",
        "verification_commands",
        "notes_for_supervisor",
    ],
    "additionalProperties": False,
}


@dataclass(slots=True)
class CompiledPrompt:
    system_prompt: str
    user_prompt: str
    schema: dict[str, Any]
    metadata: dict[str, Any]


class PromptCompiler:
    def compile(self, task: TaskRecord) -> CompiledPrompt:
        schema = task.expected_output_schema or DEFAULT_OUTPUT_SCHEMA
        system_prompt = (
            "You are a bounded worker agent. Stay inside the requested scope, "
            "follow the stop conditions, and return structured output only."
        )
        user_prompt = "\n".join(
            [
                f"Goal: {task.goal}",
                f"Task type: {task.task_type}",
                f"Scope: {task.scope}",
                f"Workspace mode: {task.workspace_mode.value}",
                f"Shared write allowed: {task.shared_write_allowed}",
                f"Allowed tools: {', '.join(task.allowed_tools) or 'none'}",
                f"Stop conditions: {', '.join(task.stop_conditions) or 'none'}",
                f"Verification expectations: {', '.join(task.verification_expectations) or 'none'}",
                f"Human notes: {', '.join(task.human_notes) or 'none'}",
                "If workspace mode is readonly, inspect only and do not modify files.",
                "Return only valid JSON that matches the provided schema.",
            ]
        )
        metadata = {
            "task_id": task.task_id,
            "goal": task.goal,
            "assigned_agent": task.assigned_agent,
            "workspace_mode": task.workspace_mode.value,
            "shared_write_allowed": task.shared_write_allowed,
            "allowed_tools": task.allowed_tools,
            "stop_conditions": task.stop_conditions,
            "verification_expectations": task.verification_expectations,
        }
        return CompiledPrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            metadata=metadata,
        )
