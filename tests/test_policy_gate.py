from pathlib import Path

from codex_claude_orchestrator.models import WorkspaceAllocation, WorkspaceMode
from codex_claude_orchestrator.policy_gate import PolicyGate


def test_guard_write_targets_blocks_readonly_and_protected_paths(tmp_path: Path):
    gate = PolicyGate(protected_paths=[".env", "secrets/"])

    readonly_allocation = WorkspaceAllocation(
        workspace_id="readonly",
        path=tmp_path,
        mode=WorkspaceMode.READONLY,
        writable=False,
    )
    isolated_allocation = WorkspaceAllocation(
        workspace_id="isolated",
        path=tmp_path,
        mode=WorkspaceMode.ISOLATED,
        writable=True,
    )
    shared_allocation = WorkspaceAllocation(
        workspace_id="shared",
        path=tmp_path,
        mode=WorkspaceMode.SHARED,
        writable=True,
    )

    readonly_decision = gate.guard_write_targets(readonly_allocation, ["app.py"])
    protected_decision = gate.guard_write_targets(isolated_allocation, [".env"])
    shared_preflight = gate.guard_workspace_execution(shared_allocation)
    shared_write = gate.guard_write_targets(shared_allocation, ["app.py"], shared_write_allowed=True)

    assert readonly_decision.allowed is False
    assert "readonly" in readonly_decision.reason
    assert protected_decision.allowed is False
    assert "protected" in protected_decision.reason
    assert shared_preflight.allowed is False
    assert "shared workspace" in shared_preflight.reason
    assert shared_write.allowed is True
