from codex_claude_orchestrator.core.models import TaskRecord, WorkspaceMode
from codex_claude_orchestrator.session.prompt_compiler import PromptCompiler


def test_compile_returns_metadata_prompt_and_schema():
    task = TaskRecord(
        task_id="task-compiler",
        parent_task_id=None,
        origin="user",
        assigned_agent="claude",
        goal="Implement the prompt compiler",
        task_type="implementation",
        scope="src/codex_claude_orchestrator",
        workspace_mode=WorkspaceMode.ISOLATED,
        allowed_tools=["Read", "Edit", "Bash"],
        stop_conditions=["Stop if tests fail twice"],
        verification_expectations=["Run pytest tests/test_prompt_compiler.py -v"],
        human_notes=["Keep diffs small"],
    )

    compiled = PromptCompiler().compile(task)

    assert compiled.metadata["goal"] == "Implement the prompt compiler"
    assert "Stop if tests fail twice" in compiled.user_prompt
    assert compiled.schema["properties"]["summary"]["type"] == "string"
