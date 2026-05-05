from codex_claude_orchestrator.mcp_server.server import create_server


def test_create_server_returns_server():
    server = create_server()
    assert server is not None
    assert server.name == "crew-orchestrator"
