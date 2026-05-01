from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from subprocess import CompletedProcess

from codex_claude_orchestrator.core.models import WorkerResult, WorkspaceAllocation
from codex_claude_orchestrator.session.prompt_compiler import CompiledPrompt


Runner = Callable[..., CompletedProcess[str]]


class ClaudeCliAdapter:
    def __init__(self, runner: Runner | None = None):
        self._runner = runner or subprocess.run

    def build_command(self, compiled: CompiledPrompt) -> list[str]:
        command = [
            "claude",
            "--print",
            compiled.user_prompt,
            "--output-format",
            "json",
            "--system-prompt",
            compiled.system_prompt,
            "--permission-mode",
            "auto",
            "--json-schema",
            json.dumps(compiled.schema, ensure_ascii=False),
        ]
        allowed_tools = compiled.metadata.get("allowed_tools") or []
        if allowed_tools:
            command.extend(["--allowedTools", ",".join(allowed_tools)])
        return command

    def execute(self, compiled: CompiledPrompt, allocation: WorkspaceAllocation) -> WorkerResult:
        command = self.build_command(compiled)
        completed = self._runner(
            command,
            cwd=str(allocation.path),
            text=True,
            capture_output=True,
            check=False,
        )
        stdout = completed.stdout or ""
        structured_output = None
        parse_error = None
        if completed.returncode == 0:
            try:
                structured_output = self._parse_structured_output(stdout)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                parse_error = str(exc)
        else:
            structured_output = None
        changed_files = []
        if isinstance(structured_output, dict):
            changed_files = list(structured_output.get("changed_files") or [])
        return WorkerResult(
            raw_output=stdout,
            stdout=stdout,
            stderr=completed.stderr or "",
            exit_code=completed.returncode,
            structured_output=structured_output,
            changed_files=changed_files,
            parse_error=parse_error,
        )

    def _parse_structured_output(self, stdout: str) -> dict[str, object] | None:
        if not stdout.strip():
            return None
        payload = json.loads(stdout)
        if isinstance(payload, dict) and "result" in payload:
            structured_output = payload.get("structured_output")
            if isinstance(structured_output, dict):
                return structured_output
            result = payload["result"]
            if isinstance(result, dict):
                return result
            if isinstance(result, str) and result.strip():
                parsed_result = json.loads(result)
                if isinstance(parsed_result, dict):
                    return parsed_result
            raise ValueError("Claude JSON envelope did not contain an object result")
        if isinstance(payload, dict):
            return payload
        raise ValueError("Claude output was not a JSON object")
