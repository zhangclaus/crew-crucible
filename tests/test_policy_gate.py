from pathlib import Path

import pytest

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


def test_guard_write_targets_allows_readonly_when_no_paths_changed(tmp_path: Path):
    gate = PolicyGate()
    readonly_allocation = WorkspaceAllocation(
        workspace_id="readonly",
        path=tmp_path,
        mode=WorkspaceMode.READONLY,
        writable=False,
    )

    decision = gate.guard_write_targets(readonly_allocation, [])

    assert decision.allowed is True


def test_guard_command_blocks_shell_command_wrappers():
    bash_decision = PolicyGate().guard_command(["bash", "-lc", "git reset --hard"])
    sh_decision = PolicyGate().guard_command(["sh", "-c", "rm -rf x"])

    assert bash_decision.allowed is False
    assert "blocked command wrapper" in bash_decision.reason
    assert sh_decision.allowed is False
    assert "blocked command wrapper" in sh_decision.reason


def test_guard_command_blocks_interpreter_inline_execution_wrappers():
    python_decision = PolicyGate().guard_command(["/usr/bin/python3", "-c", "print('x')"])
    pytest_decision = PolicyGate().guard_command([".venv/bin/python", "-m", "pytest", "-q"])

    assert python_decision.allowed is False
    assert "blocked command wrapper" in python_decision.reason
    assert pytest_decision.allowed is True


@pytest.mark.parametrize(
    "command",
    [
        ["bash", "--noprofile", "-c", "git reset --hard"],
        ["bash", "-o", "pipefail", "-c", "git reset --hard"],
        ["env", "bash", "-lc", "git reset --hard"],
        ["/usr/bin/env", "sh", "-ec", "rm -rf x"],
        ["sh", "-ec", "rm -rf x"],
        ["python3", "-I", "-c", "print('x')"],
        ["node", "--eval", "console.log('x')"],
        ["node", "--eval=console.log('x')"],
        ["node", "-pe", "process.exit(0)"],
        ["node", "-p", "1+1"],
        ["node", "--print", "1+1"],
        ["node", "--eval=process.exit(0)"],
        ["ruby", "-W0", "-e", "puts 'x'"],
        ["perl", "-Ilib", "-e", "print 'x'"],
    ],
)
def test_guard_command_blocks_inline_wrapper_variants(command):
    decision = PolicyGate().guard_command(command)

    assert decision.allowed is False
    assert "blocked command wrapper" in decision.reason


@pytest.mark.parametrize(
    "command",
    [
        ["env", "-i", "bash", "-lc", "git reset --hard"],
        ["/usr/bin/env", "--", "sh", "-c", "rm -rf x"],
        ["env", "-u", "PATH", "node", "-p", "1+1"],
        ["env", "-S", "bash -lc 'git reset --hard'"],
        ["env", "FOO=bar", "bash", "-lc", "git reset --hard"],
    ],
)
def test_guard_command_blocks_env_wrapped_inline_commands(command):
    decision = PolicyGate().guard_command(command)

    assert decision.allowed is False
    assert "blocked command wrapper" in decision.reason


@pytest.mark.parametrize(
    "command",
    [
        [".venv/bin/python", "-m", "pytest", "-q"],
        ["python3", "-m", "pytest", "-q"],
    ],
)
def test_guard_command_allows_python_module_execution(command):
    decision = PolicyGate().guard_command(command)

    assert decision.allowed is True
