from codex_claude_orchestrator.crew.models import CrewTaskStatus, WorkerRole
from codex_claude_orchestrator.crew.task_graph import TaskGraphPlanner


def test_task_graph_default_roles_are_explorer_implementer_reviewer():
    planner = TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}")

    tasks = planner.default_graph("crew-1", "Build V3 MVP")

    assert [task.role_required for task in tasks] == [
        WorkerRole.EXPLORER,
        WorkerRole.IMPLEMENTER,
        WorkerRole.REVIEWER,
    ]
    assert tasks[1].depends_on == ["task-explorer"]
    assert tasks[2].depends_on == ["task-implementer"]
    assert tasks[0].expected_outputs == ["facts", "risks", "relevant_files"]


def test_task_graph_gates_implementer_and_reviewer_until_codex_handoff():
    planner = TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}")

    tasks = planner.default_graph("crew-1", "Build V3 MVP")

    assert "Wait for Codex to send explorer findings" in tasks[1].instructions
    assert "Wait for Codex to send patch evidence" in tasks[2].instructions


def test_task_graph_assign_updates_owner_and_status():
    planner = TaskGraphPlanner(task_id_factory=lambda role: f"task-{role.value}")
    tasks = planner.default_graph("crew-1", "Build V3 MVP")

    assigned = planner.assign(tasks, "task-explorer", "worker-explorer")

    assert assigned[0].owner_worker_id == "worker-explorer"
    assert assigned[0].status == CrewTaskStatus.ASSIGNED
