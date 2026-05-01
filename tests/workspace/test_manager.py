from pathlib import Path

from codex_claude_orchestrator.core.models import TaskRecord, WorkspaceMode
from codex_claude_orchestrator.workspace.manager import WorkspaceManager


def test_isolated_workspace_copies_repo_and_detects_changes(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("print('one')\n", encoding="utf-8")

    task = TaskRecord(
        task_id="task-workspace",
        parent_task_id=None,
        origin="user",
        assigned_agent="claude",
        goal="Modify app.py",
        task_type="implementation",
        scope="repo root",
        workspace_mode=WorkspaceMode.ISOLATED,
    )

    manager = WorkspaceManager(tmp_path / ".orchestrator")
    allocation = manager.prepare(repo_root, task)

    assert allocation.path != repo_root
    (allocation.path / "app.py").write_text("print('two')\n", encoding="utf-8")
    assert manager.detect_changes(allocation) == ["app.py"]
