import json
from pathlib import Path

from codex_claude_orchestrator.crew.models import CrewRecord
from codex_claude_orchestrator.state.crew_recorder import CrewRecorder
from codex_claude_orchestrator.core.models import WorkspaceAllocation, WorkspaceMode
from codex_claude_orchestrator.workers.change_recorder import WorkerChangeRecorder


class FakeWorktreeManager:
    def __init__(self):
        self.changed_calls = []
        self.diff_calls = []

    def changed_files(self, allocation):
        self.changed_calls.append(allocation)
        return ["src/app.py"]

    def diff_patch(self, allocation):
        self.diff_calls.append(allocation)
        return "diff --git a/src/app.py b/src/app.py\n"


def test_worker_change_recorder_detects_changes_from_worktree_branch(tmp_path: Path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    recorder = CrewRecorder(tmp_path / ".orchestrator")
    recorder.start_crew(CrewRecord(crew_id="crew-1", root_goal="Build V3 MVP", repo="/repo"))
    allocation = WorkspaceAllocation(
        workspace_id="crew-1-worker-implementer",
        path=worktree,
        mode=WorkspaceMode.WORKTREE,
        writable=True,
        branch="codex/crew-1-worker-implementer",
        base_ref="base-sha",
    )
    recorder.write_text_artifact(
        "crew-1",
        "workers/worker-implementer/allocation.json",
        json.dumps(allocation.to_dict(), ensure_ascii=False),
    )

    changes = WorkerChangeRecorder(recorder, worktree_manager=FakeWorktreeManager()).record_changes(
        "crew-1", "worker-implementer", allocation
    )

    assert changes["worker_id"] == "worker-implementer"
    assert changes["branch"] == "codex/crew-1-worker-implementer"
    assert changes["changed_files"] == ["src/app.py"]
    assert changes["diff_artifact"] == "workers/worker-implementer/diff.patch"
    assert "workers/worker-implementer/diff.patch" in recorder.read_crew("crew-1")["artifacts"]
    assert recorder.read_crew("crew-1")["blackboard"][0]["type"] == "patch"
