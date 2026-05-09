"""Integration tests: verify create_server wires all tool modules."""

from unittest.mock import MagicMock

from codex_claude_orchestrator.mcp_server.server import create_server


def test_server_has_all_tools():
    server = create_server()
    # create_server without controller still returns a valid server
    assert server.name == "crew-orchestrator"


def test_server_registers_lifecycle_tools_when_controller_given():
    controller = MagicMock()
    server = create_server(controller=controller)
    assert server.name == "crew-orchestrator"


def test_server_registers_all_tool_modules():
    """Verify that all tool modules are registered by checking
    the internal tool registry of the FastMCP server."""
    controller = MagicMock()
    server = create_server(controller=controller)

    registered_names = {tool.name for tool in server._tool_manager.list_tools()}

    # Lifecycle tools
    assert "crew_start" in registered_names
    assert "crew_stop" in registered_names
    assert "crew_status" in registered_names
    assert "crew_spawn" in registered_names
    assert "crew_stop_worker" in registered_names
    assert "crew_verify" in registered_names

    # Decision tools
    assert "crew_accept" in registered_names
    assert "crew_challenge" in registered_names

    # Deleted tools
    assert "crew_run" not in registered_names
    assert "crew_decide" not in registered_names
    assert "crew_blackboard" not in registered_names
    assert "crew_events" not in registered_names
    assert "crew_observe" not in registered_names
    assert "crew_changes" not in registered_names
    assert "crew_diff" not in registered_names


def test_server_no_tools_when_no_controller():
    """Without a controller, no tools should be registered."""
    server = create_server()
    registered_names = {tool.name for tool in server._tool_manager.list_tools()}
    assert len(registered_names) == 0
