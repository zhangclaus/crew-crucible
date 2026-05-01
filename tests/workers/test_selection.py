from codex_claude_orchestrator.crew.models import WorkerRole
from codex_claude_orchestrator.workers.selection import WorkerSelectionPolicy


def test_worker_selection_quick_mode_uses_only_implementer():
    selection = WorkerSelectionPolicy().select(goal="修复 README typo", workers="auto", mode="quick")

    assert selection.roles == [WorkerRole.IMPLEMENTER]
    assert selection.mode == "quick"


def test_worker_selection_standard_mode_uses_explorer_and_implementer():
    selection = WorkerSelectionPolicy().select(goal="实现登录功能", workers="auto", mode="standard")

    assert selection.roles == [WorkerRole.EXPLORER, WorkerRole.IMPLEMENTER]
    assert selection.mode == "standard"


def test_worker_selection_full_mode_uses_all_three_roles():
    selection = WorkerSelectionPolicy().select(goal="重构检索模块并审查风险", workers="auto", mode="full")

    assert selection.roles == [WorkerRole.EXPLORER, WorkerRole.IMPLEMENTER, WorkerRole.REVIEWER]
    assert selection.mode == "full"


def test_worker_selection_auto_chooses_quick_for_tiny_doc_changes():
    selection = WorkerSelectionPolicy().select(goal="修复 README typo", workers="auto", mode="auto")

    assert selection.roles == [WorkerRole.IMPLEMENTER]
    assert selection.mode == "quick"
    assert "small" in selection.reason


def test_worker_selection_auto_chooses_full_for_project_inspection_and_improvement():
    selection = WorkerSelectionPolicy().select(
        goal="让 Claude 检查这个项目，根据 llm-wiki 思想完善代码",
        workers="auto",
        mode="auto",
    )

    assert selection.roles == [WorkerRole.EXPLORER, WorkerRole.IMPLEMENTER, WorkerRole.REVIEWER]
    assert selection.mode == "full"
    assert "review" in selection.reason


def test_worker_selection_explicit_workers_override_mode():
    selection = WorkerSelectionPolicy().select(
        goal="重构检索模块",
        workers="implementer",
        mode="full",
    )

    assert selection.roles == [WorkerRole.IMPLEMENTER]
    assert selection.mode == "explicit"
    assert "override" in selection.reason
