from codex_claude_orchestrator.adapters.claude_cli import ClaudeCliAdapter
from codex_claude_orchestrator.session.agent_registry import AgentRegistry
from codex_claude_orchestrator.core.models import WorkspaceMode


def test_default_registry_exposes_claude_profile_and_toolsets():
    registry = AgentRegistry.default()

    profile = registry.get("claude")
    readonly_tools = registry.allowed_tools(
        "claude",
        WorkspaceMode.READONLY,
        shared_write_allowed=False,
    )
    isolated_tools = registry.allowed_tools(
        "claude",
        WorkspaceMode.ISOLATED,
        shared_write_allowed=False,
    )
    shared_without_approval = registry.allowed_tools(
        "claude",
        WorkspaceMode.SHARED,
        shared_write_allowed=False,
    )

    assert profile.name == "claude"
    assert profile.adapter == "claude-cli"
    assert profile.default_workspace_mode is WorkspaceMode.ISOLATED
    assert "Read" in readonly_tools
    assert "Edit" not in readonly_tools
    assert "Edit" in isolated_tools
    assert "Edit" not in shared_without_approval


def test_default_registry_builds_claude_adapter():
    registry = AgentRegistry.default()

    adapter = registry.build_adapter("claude")

    assert isinstance(adapter, ClaudeCliAdapter)
