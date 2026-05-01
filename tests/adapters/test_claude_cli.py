from pathlib import Path
from subprocess import CompletedProcess

from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.core.models import WorkspaceAllocation, WorkspaceMode
from codex_claude_orchestrator.session.prompt_compiler import CompiledPrompt


def test_execute_uses_json_schema_and_parses_structured_output(tmp_path: Path):
    seen: dict[str, object] = {}

    def fake_runner(command: list[str], **kwargs) -> CompletedProcess[str]:
        seen["command"] = command
        seen["cwd"] = kwargs["cwd"]
        return CompletedProcess(
            args=command,
            returncode=0,
            stdout='{"type":"result","result":{"summary":"done","status":"completed","changed_files":["src/app.py"],"verification_commands":["pytest -q"],"notes_for_supervisor":[]}}',
            stderr="",
        )

    adapter = ClaudeCliAdapter(runner=fake_runner)
    compiled = CompiledPrompt(
        system_prompt="system",
        user_prompt="goal",
        schema={
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
        metadata={"task_id": "task-adapter", "allowed_tools": ["Read", "Edit"]},
    )
    allocation = WorkspaceAllocation(
        workspace_id="workspace-1",
        path=tmp_path,
        mode=WorkspaceMode.ISOLATED,
        writable=True,
    )

    result = adapter.execute(compiled, allocation)

    assert "--json-schema" in seen["command"]
    assert "--output-format" in seen["command"]
    assert "--system-prompt" in seen["command"]
    assert "--allowedTools" in seen["command"]
    assert seen["cwd"] == str(tmp_path)
    assert result.structured_output["summary"] == "done"
    assert result.changed_files == ["src/app.py"]


def test_execute_keeps_nonzero_claude_envelope_as_execution_failure(tmp_path: Path):
    def fake_runner(command: list[str], **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(
            args=command,
            returncode=1,
            stdout='{"type":"result","is_error":true,"result":"API Error: Unable to connect to API (EPERM)"}',
            stderr="",
        )

    adapter = ClaudeCliAdapter(runner=fake_runner)
    compiled = CompiledPrompt(
        system_prompt="system",
        user_prompt="goal",
        schema={"type": "object"},
        metadata={"task_id": "task-error"},
    )
    allocation = WorkspaceAllocation(
        workspace_id="workspace-1",
        path=tmp_path,
        mode=WorkspaceMode.READONLY,
        writable=False,
    )

    result = adapter.execute(compiled, allocation)

    assert result.exit_code == 1
    assert result.structured_output is None
    assert result.parse_error is None


def test_execute_reads_structured_output_field_from_claude_envelope(tmp_path: Path):
    def fake_runner(command: list[str], **kwargs) -> CompletedProcess[str]:
        return CompletedProcess(
            args=command,
            returncode=0,
            stdout='{"type":"result","result":"","structured_output":{"summary":"done","status":"completed","changed_files":[],"verification_commands":[],"notes_for_supervisor":[]}}',
            stderr="",
        )

    adapter = ClaudeCliAdapter(runner=fake_runner)
    compiled = CompiledPrompt(
        system_prompt="system",
        user_prompt="goal",
        schema={"type": "object"},
        metadata={"task_id": "task-structured-output"},
    )
    allocation = WorkspaceAllocation(
        workspace_id="workspace-1",
        path=tmp_path,
        mode=WorkspaceMode.READONLY,
        writable=False,
    )

    result = adapter.execute(compiled, allocation)

    assert result.structured_output["summary"] == "done"
    assert result.parse_error is None
